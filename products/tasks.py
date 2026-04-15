import logging
from datetime import timedelta
from math import ceil

from celery import shared_task
from branches.models import Branch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import AutoReorderRequest, Product, Supplier, SupplierReorderRequest
from .sms import normalize_phone_numbers, send_sms_via_leopard

logger = logging.getLogger(__name__)


def _auto_order_link_expiry_seconds() -> int:
    return max(int(getattr(settings, "AUTO_ORDER_LINK_EXPIRY_SECONDS", 3600) or 3600), 60)


def _site_base_url() -> str:
    return str(getattr(settings, "SITE_BASE_URL", "http://localhost:8000")).rstrip("/")


def _mail_sender() -> str:
    return str(
        getattr(
            settings,
            "AUTO_ORDER_EMAIL_FROM",
            getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@pharmtech.local"),
        )
    )


def _admin_phone_numbers(admin_email: str | None) -> list[str]:
    numbers = normalize_phone_numbers(getattr(settings, "ADMIN_PHONE", ""))
    if admin_email:
        User = get_user_model()
        user_phones = User.objects.filter(email__iexact=admin_email).exclude(phone_number="").values_list(
            "phone_number", flat=True
        )
        numbers = normalize_phone_numbers([*numbers, *user_phones])
    return numbers


def _packets_needed(deficit_units: int, pack_quantity: int) -> int:
    safe_pack_quantity = max(int(pack_quantity or 1), 1)
    safe_deficit = max(int(deficit_units or 0), 0)
    if safe_deficit <= 0:
        return 0
    return int(ceil(safe_deficit / float(safe_pack_quantity)))


@shared_task
def scan_low_stock_and_trigger_reorders():
    now = timezone.now()
    created_count = 0
    resumed_count = 0
    active_branches = list(Branch.objects.filter(is_active=True).order_by("id"))

    for product in Product.objects.filter(is_active=True).order_by("id"):
        pack_quantity = max(int(product.pack_quantity or 1), 1)
        reorder_level = product.reorder_level
        max_stock = max(int(product.max_stock or 1), 1)

        total_packets = 0
        branch_requirements = {}
        stock_by_branch_id = {
            stock.branch_id: int(stock.quantity or 0)
            for stock in product.stock_set.filter(branch__is_active=True).only("branch_id", "quantity")
        }

        for branch in active_branches:
            branch_stock = stock_by_branch_id.get(branch.id, 0)
            if branch_stock <= reorder_level:
                deficit = max(max_stock - branch_stock, 0)
                packets = _packets_needed(deficit, pack_quantity)
                if packets > 0:
                    total_packets += packets
                    branch_requirements[branch.name] = packets

        if total_packets <= 0:
            continue

        required_quantity = total_packets
        current_stock = product.current_stock()

        reorder_id = None
        with transaction.atomic():
            existing = (
                AutoReorderRequest.objects.select_for_update()
                .filter(product=product, status=AutoReorderRequest.STATUS_OPEN)
                .order_by("-created_at")
                .first()
            )

            if existing:
                has_live_supplier_request = existing.supplier_requests.filter(
                    status=SupplierReorderRequest.STATUS_PENDING,
                    expires_at__gt=now,
                ).exists()
                fields_to_update = []
                if existing.target_stock_level != max_stock:
                    existing.target_stock_level = max_stock
                    fields_to_update.append("target_stock_level")
                if existing.current_stock_snapshot != current_stock:
                    existing.current_stock_snapshot = current_stock
                    fields_to_update.append("current_stock_snapshot")
                if existing.branch_requirements != branch_requirements:
                    existing.branch_requirements = branch_requirements
                    fields_to_update.append("branch_requirements")
                if required_quantity > existing.requested_quantity:
                    existing.requested_quantity = required_quantity
                    fields_to_update.append("requested_quantity")
                if required_quantity > existing.remaining_quantity:
                    existing.remaining_quantity = required_quantity
                    fields_to_update.append("remaining_quantity")

                if fields_to_update:
                    existing.save(update_fields=[*fields_to_update, "updated_at"])

                if not has_live_supplier_request and existing.remaining_quantity > 0:
                    reorder_id = existing.id
                    resumed_count += 1
            else:
                reorder = AutoReorderRequest.objects.create(
                    product=product,
                    target_stock_level=max_stock,
                    current_stock_snapshot=current_stock,
                    requested_quantity=required_quantity,
                    remaining_quantity=required_quantity,
                    branch_requirements=branch_requirements,
                    status=AutoReorderRequest.STATUS_OPEN,
                )
                reorder_id = reorder.id
                created_count += 1

        if reorder_id:
            notify_next_supplier.delay(reorder_id)

    return {"created": created_count, "resumed": resumed_count}


@shared_task
def notify_next_supplier(reorder_request_id: int):
    now = timezone.now()

    with transaction.atomic():
        reorder = (
            AutoReorderRequest.objects.select_for_update()
            .select_related("product")
            .filter(pk=reorder_request_id)
            .first()
        )
        if not reorder:
            return {"status": "missing_reorder_request"}

        if reorder.status != AutoReorderRequest.STATUS_OPEN:
            return {"status": "inactive", "reorder_status": reorder.status}

        if reorder.remaining_quantity <= 0:
            reorder.status = AutoReorderRequest.STATUS_FULFILLED
            reorder.completed_at = now
            reorder.save(update_fields=["status", "completed_at", "updated_at"])
            return {"status": "fulfilled"}

        pending = (
            reorder.supplier_requests.select_for_update()
            .filter(status=SupplierReorderRequest.STATUS_PENDING)
            .order_by("-created_at")
            .first()
        )

        if pending:
            if now <= pending.expires_at:
                return {
                    "status": "awaiting_supplier",
                    "supplier_request_id": pending.id,
                    "supplier": pending.supplier_id,
                }

            pending.status = SupplierReorderRequest.STATUS_EXPIRED
            pending.responded_at = now
            pending.save(update_fields=["status", "responded_at", "updated_at"])

        attempted_supplier_ids = reorder.supplier_requests.values_list("supplier_id", flat=True)
        next_supplier = Supplier.objects.exclude(id__in=attempted_supplier_ids).order_by("priority", "id").first()
        supplier_priority = next_supplier.priority if next_supplier else None

        if not next_supplier:
            reorder.status = AutoReorderRequest.STATUS_EXHAUSTED
            reorder.completed_at = now
            reorder.save(update_fields=["status", "completed_at", "updated_at"])

            send_batched_exhaustion_alerts.apply_async(countdown=60)

            return {"status": "exhausted"}

        expires_at = now + timedelta(seconds=_auto_order_link_expiry_seconds())
        supplier_request = SupplierReorderRequest.objects.create(
            reorder_request=reorder,
            supplier=next_supplier,
            priority=supplier_priority or 1,
            requested_quantity=reorder.remaining_quantity,
            expires_at=expires_at,
        )

    response_path = reverse("supplier-reorder-response", kwargs={"token": supplier_request.token})
    response_link = f"{_site_base_url()}{response_path}"

    message = (
        f"Dear {supplier_request.supplier.contact_person or supplier_request.supplier.name},\n\n"
        f"Please confirm your available quantity (in PACKETS) to supply using this secure link (valid for 1 hour):\n"
        f"{response_link}\n\n"
    )

    try:
        send_mail(
            subject=f"Reorder Request",
            message=message,
            from_email=_mail_sender(),
            recipient_list=[supplier_request.supplier.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send reorder request email", extra={"supplier_request_id": supplier_request.id})
        with transaction.atomic():
            failed = SupplierReorderRequest.objects.select_for_update().filter(pk=supplier_request.id).first()
            if failed and failed.status == SupplierReorderRequest.STATUS_PENDING:
                failed.status = SupplierReorderRequest.STATUS_EMAIL_FAILED
                failed.responded_at = timezone.now()
                failed.save(update_fields=["status", "responded_at", "updated_at"])

        notify_next_supplier.delay(reorder_request_id)
        return {"status": "email_failed", "supplier_request_id": supplier_request.id}

    send_sms_via_leopard(
        message=message,
        destinations=supplier_request.supplier.phone_number,
        log_extra={"supplier_request_id": supplier_request.id},
    )

    SupplierReorderRequest.objects.filter(pk=supplier_request.id).update(emailed_at=timezone.now())
    expire_supplier_request.apply_async(args=[supplier_request.id], eta=supplier_request.expires_at)
    return {"status": "email_sent", "supplier_request_id": supplier_request.id}


@shared_task
def send_batched_exhaustion_alerts():
    admin_email = getattr(settings, "ADMIN_EMAIL", None)
    if not admin_email:
        return {"status": "no_admin_email"}

    with transaction.atomic():
        exhausted_qs = AutoReorderRequest.objects.select_for_update().select_related("product").filter(
            status=AutoReorderRequest.STATUS_EXHAUSTED,
            admin_notified=False
        )
        exhausted_requests = list(exhausted_qs)

        if not exhausted_requests:
            return {"status": "no_pending_alerts"}

        content_parts = []
        for reorder in exhausted_requests:
            supplier_responses = reorder.supplier_requests.select_related("supplier").order_by("created_at")
            summary_lines = []
            for sq in supplier_responses:
                status_text = sq.get_status_display()
                if sq.status == "expired":
                    status_text = "Ignored (Expired)"
                elif sq.status in ["partial", "accepted"]:
                    status_text = f"{status_text} - Supplied: {sq.fulfilled_quantity} / {sq.requested_quantity}"
                elif sq.status == "rejected":
                    status_text = "Rejected"
                summary_lines.append(f"  - {sq.supplier.name}: {status_text}")
            
            supplier_summary = "\n".join(summary_lines) if summary_lines else "  No suppliers were contacted."
            branch_details = []
            for b_name, b_qty in reorder.branch_requirements.items():
                branch_details.append(f"  - {b_name}: {b_qty} packet(s)")
            branch_summary = "\n".join(branch_details) if branch_details else "  None"

            content_parts.append(
                f"Product: {reorder.product.name}\n"
                f"Reorder Request ID: {reorder.pk}\n"
                f"Missing Quantity: {reorder.remaining_quantity} packet(s)\n"
                f"Branch Breakdown:\n{branch_summary}\n"
                f"Supplier Activity Log:\n{supplier_summary}\n"
                f"{'-'*40}"
            )

            reorder.admin_notified = True
            reorder.save(update_fields=["admin_notified", "updated_at"])

    if content_parts:
        admin_message = (
            f"The following {len(content_parts)} products have exhausted all listed suppliers or failed to respond.\n"
            "Manual intervention is now required.\n\n"
            + "\n\n".join(content_parts)
        )

        try:
            send_mail(
                subject=f"URGENT: Reorder Failed for {len(content_parts)} product(s)",
                message=admin_message,
                from_email=_mail_sender(),
                recipient_list=[admin_email],
                fail_silently=True,
            )
        except Exception:
            logger.exception("Failed to send batched admin exhaustion alert")

        send_sms_via_leopard(
            message=admin_message,
            destinations=_admin_phone_numbers(admin_email),
            log_extra={"batch_alert": True},
        )

    return {"status": "alerts_sent", "count": len(content_parts)}


@shared_task
def expire_supplier_request(supplier_request_id: int):
    now = timezone.now()
    reorder_id = None

    with transaction.atomic():
        supplier_request = (
            SupplierReorderRequest.objects.select_for_update()
            .select_related("reorder_request")
            .filter(pk=supplier_request_id)
            .first()
        )
        if not supplier_request:
            return {"status": "missing_supplier_request"}

        if supplier_request.status != SupplierReorderRequest.STATUS_PENDING:
            return {"status": "already_processed", "supplier_request_status": supplier_request.status}

        if now <= supplier_request.expires_at:
            return {"status": "not_expired_yet", "expires_at": supplier_request.expires_at.isoformat()}

        supplier_request.status = SupplierReorderRequest.STATUS_EXPIRED
        supplier_request.responded_at = now
        supplier_request.save(update_fields=["status", "responded_at", "updated_at"])

        reorder = supplier_request.reorder_request
        if reorder.status == AutoReorderRequest.STATUS_OPEN and reorder.remaining_quantity > 0:
            reorder_id = reorder.id

    if reorder_id:
        notify_next_supplier.delay(reorder_id)

    return {"status": "expired", "supplier_request_id": supplier_request_id}
