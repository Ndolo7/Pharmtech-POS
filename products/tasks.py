import logging
import re
from collections import defaultdict
from datetime import datetime, time as dt_time, timedelta
from io import BytesIO
from math import ceil
from pathlib import Path

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from branches.models import Branch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage, send_mail
from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageDraw

from .models import AutoReorderRequest, BranchSupplyRequest, Product, Purchase, Stock, Supplier, SupplierReorderRequest
from .sms import normalize_phone_numbers, send_sms_via_leopard

logger = logging.getLogger(__name__)


def _auto_order_link_expiry_seconds() -> int:
    return max(int(getattr(settings, "AUTO_ORDER_LINK_EXPIRY_SECONDS", 3600) or 3600), 60)


def _auto_order_scan_window_minutes() -> int:
    configured = getattr(settings, "AUTO_ORDER_SCAN_WINDOW_MINUTES", 5)
    try:
        return max(int(configured or 5), 1)
    except (TypeError, ValueError):
        return 5


def _is_within_scan_window(now_local, target_time: dt_time, window_minutes: int) -> bool:
    target_dt = datetime.combine(now_local.date(), target_time, tzinfo=now_local.tzinfo)
    delta_seconds = abs((now_local - target_dt).total_seconds())
    return delta_seconds <= (window_minutes * 60)


def _is_appointed_scan_time(now_local) -> bool:
    from .models import AutoOrderScheduleSetting

    config = AutoOrderScheduleSetting.objects.order_by("id").first()
    daily_time = config.daily_run_time if config else dt_time(8, 0)
    sunday_time = config.sunday_run_time if config else dt_time(11, 0)
    window_minutes = _auto_order_scan_window_minutes()

    if _is_within_scan_window(now_local, daily_time, window_minutes):
        return True

    # Sunday-only dedicated scan.
    if now_local.weekday() == 6 and _is_within_scan_window(now_local, sunday_time, window_minutes):
        return True

    return False


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


def _auto_reorder_target_units(max_stock_units: int, current_stock_units: int, pack_quantity: int) -> int:
    """Return reorder target units when low-stock trigger is active.

    Business rule:
    - pack_quantity > 1: reorder full max stock quantity.
    - pack_quantity == 1: reorder only deficit to max stock.
    """
    safe_pack_quantity = max(int(pack_quantity or 1), 1)
    safe_max_stock = max(int(max_stock_units or 1), 1)
    safe_current_stock = max(int(current_stock_units or 0), 0)

    if safe_pack_quantity > 1:
        return safe_max_stock
    return max(safe_max_stock - safe_current_stock, 0)


def _auto_order_stale_days() -> int:
    try:
        return max(int(getattr(settings, "AUTO_ORDER_STALE_DAYS", 75) or 75), 1)
    except (TypeError, ValueError):
        return 75


def _is_primary_live_supplier_request(supplier_request: SupplierReorderRequest, now=None) -> bool:
    reference_time = now or timezone.now()
    first_live_request_id = (
        SupplierReorderRequest.objects.filter(
            supplier_id=supplier_request.supplier_id,
            status=SupplierReorderRequest.STATUS_PENDING,
            expires_at__gt=reference_time,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)
        .first()
    )
    return bool(first_live_request_id == supplier_request.id)


def _has_auto_notification_sent_today_for_supplier(supplier_id: int, now=None) -> bool:
    reference_time = now or timezone.now()
    today_local = timezone.localdate(reference_time)
    return SupplierReorderRequest.objects.filter(
        supplier_id=supplier_id,
        reorder_request__origin=AutoReorderRequest.ORIGIN_AUTO,
        emailed_at__date=today_local,
    ).exists()


def _sanitize_filename_fragment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("_") or "purchase"


def _svg_path_to_points(path_data: str) -> list[tuple[float, float]]:
    tokens = [token for token in re.findall(r"[MLHVZ]|-?\d+(?:\.\d+)?", path_data or "") if token]
    points: list[tuple[float, float]] = []
    idx = 0
    command = None
    current_x = 0.0
    current_y = 0.0

    while idx < len(tokens):
        token = tokens[idx]
        if token in {"M", "L", "H", "V", "Z"}:
            command = token
            idx += 1
            if command == "Z":
                break
            continue

        if command in {"M", "L"}:
            if idx + 1 >= len(tokens):
                break
            current_x = float(tokens[idx])
            current_y = float(tokens[idx + 1])
            points.append((current_x, current_y))
            idx += 2
            continue

        if command == "H":
            current_x = float(tokens[idx])
            points.append((current_x, current_y))
            idx += 1
            continue

        if command == "V":
            current_y = float(tokens[idx])
            points.append((current_x, current_y))
            idx += 1
            continue

        idx += 1

    return points


def _ziada_logo_png_bytes() -> bytes:
    logo_svg_path = Path(settings.BASE_DIR) / "static" / "branding" / "logo.svg"
    if not logo_svg_path.exists():
        return b""

    try:
        content = logo_svg_path.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Unable to read logo SVG for purchase confirmation PDF", extra={"path": str(logo_svg_path)})
        return b""

    width_match = re.search(r'width="([\d.]+)"', content)
    height_match = re.search(r'height="([\d.]+)"', content)
    canvas_width = int(float(width_match.group(1))) if width_match else 544
    canvas_height = int(float(height_match.group(1))) if height_match else 542

    path_matches = re.findall(r'<path[^>]*d="([^"]+)"[^>]*fill="([^"]+)"', content)
    if not path_matches:
        return b""

    image = Image.new("RGBA", (canvas_width, canvas_height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image, "RGBA")

    for path_data, fill in path_matches:
        points = _svg_path_to_points(path_data)
        if len(points) < 3:
            continue
        draw.polygon(points, fill=fill)

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _purchase_confirmation_sms_text(purchase: Purchase) -> str:
    return (
        f"Stock receipt for invoice {purchase.invoice_number} has been confirmed. "
        f"A PDF confirmation has been emailed to {purchase.supplier.email}."
    )


def _supplier_reorder_response_link(supplier_request: SupplierReorderRequest) -> str:
    response_path = reverse("supplier-reorder-response", kwargs={"token": supplier_request.token})
    return f"{_site_base_url()}{response_path}"


def _supplier_reorder_message_text(supplier_request: SupplierReorderRequest, response_link: str) -> str:
    return (
        f"Dear {supplier_request.supplier.contact_person or supplier_request.supplier.name},\n\n"
        "Please click this link. Supply requested. What you do have, tick it. You must respond within 3 hours:\n"
        f"{response_link}\n\n"
    )


def _supplier_reorder_batch_message_text(
    supplier_requests: list[SupplierReorderRequest],
    *,
    contact_name: str,
) -> str:
    intro = (
        f"Dear {contact_name},\n\n"
        "Please review and respond to the following manual order request(s) within 1 hour:\n\n"
    )
    lines = []
    for index, supplier_request in enumerate(supplier_requests, start=1):
        response_link = _supplier_reorder_response_link(supplier_request)
        product_name = supplier_request.reorder_request.product.name
        requested_quantity = supplier_request.requested_quantity
        lines.append(f"{index}. {product_name} - {requested_quantity} packet(s)\n{response_link}")
    return intro + "\n\n".join(lines) + "\n\n"


def _get_or_create_unregistered_supplier(unregistered_name: str) -> Supplier:
    canonical_name = "Unregistered Supplier"
    existing = Supplier.objects.filter(name__iexact=canonical_name).order_by("id").first()
    if existing:
        return existing

    return Supplier.objects.create(
        name=canonical_name,
        contact_person=canonical_name,
        phone_number="0000000000",
        email="unregistered-supplier@example.com",
        address="Unregistered supplier",
        priority=9999,
    )


def _branch_last_sale_dates(product_id: int) -> dict[int, timezone.datetime]:
    from sales.models import SaleItem

    sale_rows = (
        SaleItem.objects.filter(product_id=product_id, sale__branch__is_active=True)
        .values("sale__branch_id")
        .annotate(last_sale=Max("sale__created_at"))
    )
    return {
        int(row["sale__branch_id"]): row["last_sale"]
        for row in sale_rows
        if row.get("sale__branch_id") and row.get("last_sale")
    }


def _supplier_branch_ids_for_reorder(reorder: AutoReorderRequest) -> set[int]:
    branch_names = [
        str(branch_name)
        for branch_name, qty in (reorder.branch_requirements or {}).items()
        if max(int(qty or 0), 0) > 0
    ]
    if not branch_names:
        return set()
    return set(Branch.objects.filter(is_active=True, name__in=branch_names).values_list("id", flat=True))


def _pick_stale_supply_branch(product: Product, demand_branch: Branch, active_branches: list[Branch], stock_by_branch_id: dict[int, int], now_local) -> Branch | None:
    stale_days = _auto_order_stale_days()
    last_sale_dates = _branch_last_sale_dates(product.id)
    stale_candidates = []
    for branch in active_branches:
        if branch.id == demand_branch.id:
            continue
        stock_qty = max(int(stock_by_branch_id.get(branch.id, 0) or 0), 0)
        if stock_qty <= 0:
            continue
        last_sale = last_sale_dates.get(branch.id)
        if last_sale is None:
            continue
        last_sale_local_date = timezone.localtime(last_sale, now_local.tzinfo).date()
        days_since_sale = (now_local.date() - last_sale_local_date).days
        if days_since_sale < stale_days:
            continue
        stale_candidates.append((days_since_sale, stock_qty, branch))

    if not stale_candidates:
        return None

    stale_candidates.sort(key=lambda item: (item[0], item[1], item[2].name.lower()), reverse=True)
    return stale_candidates[0][2]


def _branch_supply_request_message_text(branch_request: BranchSupplyRequest) -> str:
    return (
        f"ZiadaRx branch stock request: {branch_request.destination_branch.name} needs "
        f"{branch_request.requested_quantity} packet(s) of {branch_request.product.name}. "
        f"Please confirm if {branch_request.source_branch.name} can supply."
    )


def _send_branch_supply_request_sms(branch_request: BranchSupplyRequest) -> dict:
    sms_result = send_sms_via_leopard(
        message=_branch_supply_request_message_text(branch_request),
        destinations=branch_request.source_branch.phone_number,
        log_extra={"branch_supply_request_id": branch_request.id, "event": "branch_supply_request_notification"},
    )
    if sms_result.get("success"):
        return {"status": "sms_sent", "branch_supply_request_id": branch_request.id}

    reason = str(sms_result.get("reason") or "").strip()
    if reason in {"not_configured", "no_recipients"}:
        logger.warning(
            "Branch supply request SMS skipped",
            extra={"branch_supply_request_id": branch_request.id, "reason": reason},
        )
        return {"status": "sms_skipped", "reason": reason, "branch_supply_request_id": branch_request.id}

    logger.warning(
        "Branch supply request SMS failed",
        extra={"branch_supply_request_id": branch_request.id, "reason": reason or "unknown"},
    )
    return {"status": "sms_failed", "reason": reason or "unknown", "branch_supply_request_id": branch_request.id}


def _upsert_branch_supply_request(product: Product, source_branch: Branch, destination_branch: Branch, requested_quantity: int, now, created_by=None):
    request = (
        BranchSupplyRequest.objects.select_for_update()
        .filter(
            product=product,
            source_branch=source_branch,
            destination_branch=destination_branch,
            status=BranchSupplyRequest.STATUS_PENDING,
        )
        .order_by("-created_at")
        .first()
    )
    if request:
        if requested_quantity > request.requested_quantity:
            request.requested_quantity = requested_quantity
            request.save(update_fields=["requested_quantity", "updated_at"])
            return request, True
        return request, False

    request = BranchSupplyRequest.objects.create(
        product=product,
        source_branch=source_branch,
        destination_branch=destination_branch,
        requested_quantity=requested_quantity,
        created_by=created_by,
        status=BranchSupplyRequest.STATUS_PENDING,
        notes=(
            f"Auto-created because {product.name} has been idle at {source_branch.name} for at least {_auto_order_stale_days()} days."
        ),
    )
    return request, True


def _send_supplier_reorder_sms_once(supplier_request: SupplierReorderRequest, message: str | None = None) -> dict:
    sms_body = message or _supplier_reorder_message_text(
        supplier_request,
        _supplier_reorder_response_link(supplier_request),
    )
    sms_result = send_sms_via_leopard(
        message=sms_body,
        destinations=supplier_request.supplier.phone_number,
        log_extra={"supplier_request_id": supplier_request.id, "event": "supplier_reorder_notification"},
    )
    if sms_result.get("success"):
        return {"status": "sms_sent", "supplier_request_id": supplier_request.id}

    reason = str(sms_result.get("reason") or "").strip()
    if reason in {"not_configured", "no_recipients"}:
        logger.warning(
            "Reorder request SMS skipped",
            extra={"supplier_request_id": supplier_request.id, "reason": reason},
        )
        return {"status": "sms_skipped", "supplier_request_id": supplier_request.id, "reason": reason}

    return {
        "status": "sms_failed",
        "supplier_request_id": supplier_request.id,
        "reason": reason or "provider_unsuccessful_response",
        "sms_result": sms_result,
    }


def _send_purchase_confirmation_sms_once(purchase: Purchase) -> dict:
    sms_result = send_sms_via_leopard(
        message=_purchase_confirmation_sms_text(purchase),
        destinations=purchase.supplier.phone_number,
        log_extra={"purchase_id": purchase.id, "event": "stock_receipt_confirmation"},
    )
    if sms_result.get("success"):
        return {"status": "sms_sent", "purchase_id": purchase.id}

    reason = str(sms_result.get("reason") or "").strip()
    if reason in {"not_configured", "no_recipients"}:
        logger.warning(
            "Purchase confirmation SMS skipped",
            extra={"purchase_id": purchase.id, "reason": reason},
        )
        return {"status": "sms_skipped", "purchase_id": purchase.id, "reason": reason}

    return {
        "status": "sms_failed",
        "purchase_id": purchase.id,
        "reason": reason or "provider_unsuccessful_response",
        "sms_result": sms_result,
    }


def _build_purchase_confirmation_pdf(purchase: Purchase) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as RLImage
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    brand_style = ParagraphStyle(
        "BrandHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=20,
        textColor=colors.HexColor("#1E3A8A"),
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "ReceiptSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=13,
        textColor=colors.HexColor("#334155"),
    )
    story = []

    logo_bytes = _ziada_logo_png_bytes()
    if logo_bytes:
        logo = RLImage(BytesIO(logo_bytes), width=28 * mm, height=28 * mm)
        logo.hAlign = "LEFT"
        story.append(logo)
        story.append(Spacer(1, 4))

    story.append(Paragraph("Ziadapharma", brand_style))
    story.append(Paragraph("Purchase Order Receipt", subtitle_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Supplier: {purchase.supplier.name}", styles["Normal"]))
    story.append(Paragraph(f"Invoice Number: {purchase.invoice_number}", styles["Normal"]))
    story.append(Paragraph(f"Branch: {purchase.branch.name}", styles["Normal"]))
    story.append(
        Paragraph(
            f"Received At: {timezone.localtime(purchase.created_at).strftime('%Y-%m-%d %H:%M:%S')}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 12))

    rows = [["Product", "Quantity (Units)", "Unit Cost", "Line Total"]]
    for item in purchase.items.select_related("product").all():
        rows.append(
            [
                item.product.name,
                str(item.quantity),
                f"KES {item.unit_cost:.2f}",
                f"KES {item.total_cost:.2f}",
            ]
        )

    rows.append(["", "", "Grand Total", f"KES {purchase.total_amount:.2f}"])

    table = Table(rows, colWidths=[80 * mm, 30 * mm, 32 * mm, 32 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF6FF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ]
        )
    )
    story.append(table)

    doc.build(story)
    return buffer.getvalue()


@shared_task
def scan_low_stock_and_trigger_reorders():
    from sales.models import SaleItem

    now = timezone.now()
    now_local = timezone.localtime(now)
    yesterday_local_date = (now_local - timedelta(days=1)).date()
    if getattr(settings, "AUTO_ORDER_ENFORCE_APPOINTED_WINDOW", True) and not _is_appointed_scan_time(now_local):
        logger.info(
            "Skipping auto-reorder scan outside appointed schedule window",
            extra={"now": now_local.isoformat()},
        )
        return {"status": "skipped_outside_schedule", "created": 0, "resumed": 0, "cancelled_unsold": 0}

    created_count = 0
    resumed_count = 0
    cancelled_unsold_count = 0
    internal_supply_created = 0
    active_branches = list(Branch.objects.filter(is_active=True).order_by("id"))
    active_branch_by_id = {branch.id: branch for branch in active_branches}
    sold_branches_by_product = defaultdict(set)
    sold_product_branch_pairs = (
        SaleItem.objects.filter(
            product__is_active=True,
            sale__branch__is_active=True,
            sale__created_at__date=yesterday_local_date,
        )
        .values_list("product_id", "sale__branch_id")
        .distinct()
    )
    for product_id, branch_id in sold_product_branch_pairs:
        if branch_id in active_branch_by_id:
            sold_branches_by_product[int(product_id)].add(int(branch_id))
    sold_product_ids = set(sold_branches_by_product.keys())

    stale_days = _auto_order_stale_days()
    stale_cutoff = now - timedelta(days=stale_days)

    # ── Per-branch stale exemption ───────────────────────────────────────────
    # Mark each branch-product Stock row as exempt when that branch currently has
    # positive stock (quantity > 0) but has had no sale of that product in the last
    # stale_days. Products with zero stock or that have never been stocked in the branch
    # are NOT auto-exempted.
    recent_sale_subquery = SaleItem.objects.filter(
        product_id=OuterRef("product_id"),
        sale__branch_id=OuterRef("branch_id"),
        sale__created_at__gte=stale_cutoff,
    ).values("id")[:1]
    Stock.objects.filter(
        product__is_active=True,
        branch__is_active=True,
        quantity__gt=0,
        exempt_from_auto_reorder=False,
    ).exclude(
        Exists(recent_sale_subquery)
    ).update(exempt_from_auto_reorder=True)

    if sold_product_ids:
        cancelled_unsold_count = AutoReorderRequest.objects.filter(
            status=AutoReorderRequest.STATUS_OPEN,
            origin=AutoReorderRequest.ORIGIN_AUTO,
        ).exclude(product_id__in=sold_product_ids).update(
            status=AutoReorderRequest.STATUS_CANCELLED,
            completed_at=now,
            updated_at=now,
        )
    else:
        cancelled_unsold_count = AutoReorderRequest.objects.filter(
            status=AutoReorderRequest.STATUS_OPEN,
            origin=AutoReorderRequest.ORIGIN_AUTO,
        ).update(
            status=AutoReorderRequest.STATUS_CANCELLED,
            completed_at=now,
            updated_at=now,
        )

    for product in Product.objects.filter(
        is_active=True,
        exempt_from_auto_reorder=False,
        id__in=sold_product_ids,
    ).order_by("id"):
        pack_quantity = max(int(product.pack_quantity or 1), 1)
        reorder_level = product.reorder_level
        max_stock = max(int(product.max_stock or 1), 1)
        sold_branch_ids = sold_branches_by_product.get(product.id, set())
        if not sold_branch_ids:
            continue

        supplier_branch_requirements = {}
        exempt_branch_ids: set[int] = set()
        stock_by_branch_id: dict[int, int] = {}
        for stock_row in product.stock_set.filter(branch__is_active=True).only(
            "branch_id", "quantity", "exempt_from_auto_reorder"
        ):
            stock_by_branch_id[stock_row.branch_id] = int(stock_row.quantity or 0)
            if stock_row.exempt_from_auto_reorder:
                exempt_branch_ids.add(stock_row.branch_id)
        last_sale_dates = _branch_last_sale_dates(product.id)
        internal_supply_created_for_branches = set()

        for branch_id in sorted(sold_branch_ids):
            branch = active_branch_by_id.get(branch_id)
            if not branch:
                continue
            if branch_id in exempt_branch_ids:
                # This branch has been stale for this product; it is not a
                # demand source even though it sold yesterday (the un-exemption
                # step above would have cleared it if that sale occurred, so
                # reaching here means the Stock row is still marked stale).
                continue
            branch_stock = stock_by_branch_id.get(branch_id, 0)
            if branch_stock > reorder_level:
                continue

            target_units = _auto_reorder_target_units(
                max_stock_units=max_stock,
                current_stock_units=branch_stock,
                pack_quantity=pack_quantity,
            )
            packets = _packets_needed(target_units, pack_quantity)
            if packets <= 0:
                continue

            supply_branch = _pick_stale_supply_branch(product, branch, active_branches, stock_by_branch_id, now_local)
            if supply_branch:
                with transaction.atomic():
                    branch_request, should_notify_branch = _upsert_branch_supply_request(product, supply_branch, branch, packets, now)
                if should_notify_branch:
                    _send_branch_supply_request_sms(branch_request)
                internal_supply_created += 1
                internal_supply_created_for_branches.add(branch.name)
                continue

            supplier_branch_requirements[branch.name] = packets

        if not supplier_branch_requirements:
            continue

        required_quantity = sum(supplier_branch_requirements.values())
        current_stock = product.current_stock()

        reorder_id = None
        with transaction.atomic():
            # Serialize per-product reorder scan work to prevent duplicate open
            # auto orders when multiple scan tasks run concurrently.
            Product.objects.select_for_update().filter(pk=product.pk).only("id").first()

            existing = (
                AutoReorderRequest.objects.select_for_update()
                .filter(
                    product=product,
                    status=AutoReorderRequest.STATUS_OPEN,
                    origin=AutoReorderRequest.ORIGIN_AUTO,
                )
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
                if existing.branch_requirements != supplier_branch_requirements:
                    existing.branch_requirements = supplier_branch_requirements
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
                    origin=AutoReorderRequest.ORIGIN_AUTO,
                    branch_requirements=supplier_branch_requirements,
                    status=AutoReorderRequest.STATUS_OPEN,
                )
                reorder_id = reorder.id
                created_count += 1

        if reorder_id:
            notify_next_supplier.delay(reorder_id)

    return {
        "created": created_count,
        "resumed": resumed_count,
        "cancelled_unsold": cancelled_unsold_count,
        "branch_supply_requests": internal_supply_created,
    }


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

        if reorder.origin == AutoReorderRequest.ORIGIN_MANUAL and reorder.approval_status == AutoReorderRequest.APPROVAL_PENDING:
            return {"status": "awaiting_approval", "reorder_status": reorder.status}

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

        if reorder.origin == AutoReorderRequest.ORIGIN_MANUAL and reorder.unregistered_supplier_name:
            unregistered_supplier = _get_or_create_unregistered_supplier(reorder.unregistered_supplier_name)
            supplier_request = (
                reorder.supplier_requests.select_for_update()
                .filter(supplier=unregistered_supplier)
                .order_by("-created_at", "-id")
                .first()
            )
            if not supplier_request:
                supplier_request = SupplierReorderRequest.objects.create(
                    reorder_request=reorder,
                    supplier=unregistered_supplier,
                    priority=unregistered_supplier.priority or 9999,
                    requested_quantity=reorder.remaining_quantity,
                    fulfilled_quantity=reorder.remaining_quantity,
                    status=SupplierReorderRequest.STATUS_ACCEPTED,
                    expires_at=now + timedelta(seconds=_auto_order_link_expiry_seconds()),
                    responded_at=now,
                    emailed_at=now,
                )
            else:
                supplier_request.status = SupplierReorderRequest.STATUS_ACCEPTED
                supplier_request.fulfilled_quantity = reorder.remaining_quantity
                supplier_request.responded_at = now
                supplier_request.save(update_fields=["status", "fulfilled_quantity", "responded_at", "updated_at"])

            reorder.remaining_quantity = 0
            reorder.status = AutoReorderRequest.STATUS_FULFILLED
            reorder.completed_at = now
            reorder.save(update_fields=["remaining_quantity", "status", "completed_at", "updated_at"])
            return {
                "status": "manual_unregistered_supplier_ready_for_receiving",
                "supplier_name": unregistered_supplier.name,
                "supplier_id": unregistered_supplier.id,
                "supplier_request_id": supplier_request.id,
            }

        attempted_supplier_ids = reorder.supplier_requests.values_list("supplier_id", flat=True)
        supplier_candidates = Supplier.objects.all()
        reorder_branch_ids = _supplier_branch_ids_for_reorder(reorder)
        if reorder_branch_ids:
            supplier_candidates = supplier_candidates.filter(Q(branch__isnull=True) | Q(branch_id__in=reorder_branch_ids))
        if reorder.origin == AutoReorderRequest.ORIGIN_MANUAL and reorder.preferred_supplier_ids:
            supplier_candidates = supplier_candidates.filter(id__in=reorder.preferred_supplier_ids)

        next_supplier = supplier_candidates.exclude(id__in=attempted_supplier_ids).order_by("priority", "id").first()
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

    reference_now = timezone.now()
    is_manual_reorder = supplier_request.reorder_request.origin == AutoReorderRequest.ORIGIN_MANUAL
    is_primary_live_request = _is_primary_live_supplier_request(supplier_request, now=reference_now)

    if not is_primary_live_request:
        expire_supplier_request.apply_async(args=[supplier_request.id], eta=supplier_request.expires_at)
        return {
            "status": "notification_suppressed_existing_pending_supplier",
            "supplier_request_id": supplier_request.id,
            "sms_status": "skipped_existing_pending_supplier_notification",
        }

    if not is_manual_reorder and _has_auto_notification_sent_today_for_supplier(
        supplier_request.supplier_id,
        now=reference_now,
    ):
        expire_supplier_request.apply_async(args=[supplier_request.id], eta=supplier_request.expires_at)
        return {
            "status": "notification_suppressed_daily_limit",
            "supplier_request_id": supplier_request.id,
            "sms_status": "skipped_existing_pending_supplier_notification",
        }

    message = ""
    supplier_requests_to_mark: list[SupplierReorderRequest] = [supplier_request]
    if is_manual_reorder:
        supplier_requests_to_mark = list(
            SupplierReorderRequest.objects.select_related("reorder_request__product", "supplier")
            .filter(
                supplier_id=supplier_request.supplier_id,
                status=SupplierReorderRequest.STATUS_PENDING,
                emailed_at__isnull=True,
                reorder_request__origin=AutoReorderRequest.ORIGIN_MANUAL,
            )
            .order_by("created_at", "id")
        )
        if not any(req.id == supplier_request.id for req in supplier_requests_to_mark):
            expire_supplier_request.apply_async(args=[supplier_request.id], eta=supplier_request.expires_at)
            return {
                "status": "notification_suppressed_existing_pending_supplier",
                "supplier_request_id": supplier_request.id,
                "sms_status": "skipped_existing_pending_supplier_notification",
            }
        message = _supplier_reorder_batch_message_text(
            supplier_requests_to_mark,
            contact_name=supplier_request.supplier.contact_person or supplier_request.supplier.name,
        )
    else:
        response_link = _supplier_reorder_response_link(supplier_request)
        message = _supplier_reorder_message_text(supplier_request, response_link)

    try:
        send_mail(
            subject="Purchase Order",
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

    sms_outcome = _send_supplier_reorder_sms_once(supplier_request, message)
    sms_status = sms_outcome.get("status")
    if sms_status == "sms_failed":
        logger.warning(
            "Immediate reorder request SMS failed; queued retry task",
            extra={
                "supplier_request_id": supplier_request.id,
                "reason": sms_outcome.get("reason"),
                "sms_result": sms_outcome.get("sms_result"),
            },
        )
        send_supplier_reorder_sms.delay(supplier_request.id)
        sms_status = "queued_retry"

    mark_ids = [req.id for req in supplier_requests_to_mark]
    SupplierReorderRequest.objects.filter(pk__in=mark_ids).update(emailed_at=timezone.now())
    for marked_request in supplier_requests_to_mark:
        expire_supplier_request.apply_async(args=[marked_request.id], eta=marked_request.expires_at)
    return {"status": "email_sent", "supplier_request_id": supplier_request.id, "sms_status": sms_status}


@shared_task
def send_batched_exhaustion_alerts():
    admin_email = getattr(settings, "ADMIN_EMAIL", None)
    if not admin_email:
        return {"status": "no_admin_email"}

    exhausted_requests = list(
        AutoReorderRequest.objects.select_related("product")
        .filter(
            status=AutoReorderRequest.STATUS_EXHAUSTED,
            admin_notified=False,
        )
        .order_by("id")
    )
    if not exhausted_requests:
        return {"status": "no_pending_alerts"}

    content_parts = []
    reorder_ids = []
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
        for b_name, b_qty in (reorder.branch_requirements or {}).items():
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
        reorder_ids.append(reorder.id)

    admin_message = (
        f"The following {len(content_parts)} products have exhausted all listed suppliers or failed to respond.\n"
        "Manual intervention is now required.\n\n"
        + "\n\n".join(content_parts)
    )

    try:
        sent_count = send_mail(
            subject=f"URGENT: Reorder Failed for {len(content_parts)} product(s)",
            message=admin_message,
            from_email=_mail_sender(),
            recipient_list=[admin_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send batched admin exhaustion alert email")
        return {"status": "email_failed", "count": len(content_parts)}

    if int(sent_count or 0) < 1:
        logger.error(
            "Admin exhaustion alert email was not delivered",
            extra={"reorder_ids": reorder_ids, "admin_email": admin_email},
        )
        return {"status": "email_failed", "count": len(content_parts)}

    send_sms_via_leopard(
        message=admin_message,
        destinations=_admin_phone_numbers(admin_email),
        log_extra={"batch_alert": True},
    )

    AutoReorderRequest.objects.filter(
        id__in=reorder_ids,
        status=AutoReorderRequest.STATUS_EXHAUSTED,
        admin_notified=False,
    ).update(admin_notified=True, updated_at=timezone.now())

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


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_supplier_reorder_sms(self, supplier_request_id: int):
    supplier_request = (
        SupplierReorderRequest.objects.select_related("supplier")
        .filter(pk=supplier_request_id)
        .first()
    )
    if not supplier_request:
        return {"status": "missing_supplier_request"}
    if supplier_request.status != SupplierReorderRequest.STATUS_PENDING:
        return {
            "status": "inactive",
            "supplier_request_id": supplier_request.id,
            "supplier_request_status": supplier_request.status,
        }
    if timezone.now() > supplier_request.expires_at:
        return {"status": "expired", "supplier_request_id": supplier_request.id}

    sms_outcome = _send_supplier_reorder_sms_once(supplier_request)
    if sms_outcome.get("status") in {"sms_sent", "sms_skipped"}:
        return sms_outcome

    logger.warning(
        "Reorder request SMS failed; scheduling retry",
        extra={
            "supplier_request_id": supplier_request.id,
            "reason": sms_outcome.get("reason"),
            "retries": self.request.retries,
            "sms_result": sms_outcome.get("sms_result"),
        },
    )
    retry_delay_seconds = min(300, 60 * (2 ** max(self.request.retries, 0)))
    try:
        raise self.retry(countdown=retry_delay_seconds)
    except MaxRetriesExceededError:
        logger.error(
            "Reorder request SMS retries exhausted",
            extra={
                "supplier_request_id": supplier_request.id,
                "reason": sms_outcome.get("reason"),
                "sms_result": sms_outcome.get("sms_result"),
            },
        )
        return {
            "status": "sms_failed",
            "supplier_request_id": supplier_request.id,
            "reason": sms_outcome.get("reason") or "max_retries_exceeded",
        }


@shared_task
def send_purchase_confirmation_to_supplier(purchase_id: int):
    with transaction.atomic():
        purchase = (
            Purchase.objects.select_for_update()
            .select_related("supplier", "branch", "created_by")
            .prefetch_related("items__product")
            .filter(pk=purchase_id)
            .first()
        )
        if not purchase:
            return {"status": "missing_purchase"}

        if purchase.supplier_confirmation_emailed_at:
            return {
                "status": "already_sent",
                "purchase_id": purchase.id,
                "sent_at": purchase.supplier_confirmation_emailed_at.isoformat(),
            }

        supplier_email = (purchase.supplier.email or "").strip()
        if not supplier_email:
            return {"status": "missing_supplier_email", "purchase_id": purchase.id}

        pdf_bytes = _build_purchase_confirmation_pdf(purchase)
        filename = f"stock_confirmation_{_sanitize_filename_fragment(purchase.invoice_number)}.pdf"

        body = (
            f"Dear {purchase.supplier.contact_person or purchase.supplier.name},\n\n"
            "Please find attached the stock receipt confirmation PDF for the received order.\n\n"
            f"Invoice Number: {purchase.invoice_number}\n"
            f"Branch: {purchase.branch.name}\n"
            f"Total Amount: KES {purchase.total_amount:.2f}\n\n"
            "Regards,\n"
            "ZiadaRx"
        )

        email = EmailMessage(
            subject=f"Stock Receipt Confirmation - {purchase.invoice_number}",
            body=body,
            from_email=_mail_sender(),
            to=[supplier_email],
        )
        email.attach(filename, pdf_bytes, "application/pdf")
        email.send(fail_silently=False)

        purchase.supplier_confirmation_emailed_at = timezone.now()
        purchase.save(update_fields=["supplier_confirmation_emailed_at"])

        sms_outcome = _send_purchase_confirmation_sms_once(purchase)
        sms_status = sms_outcome.get("status")
        if sms_status == "sms_failed":
            logger.warning(
                "Immediate purchase confirmation SMS failed; queued retry task",
                extra={
                    "purchase_id": purchase.id,
                    "reason": sms_outcome.get("reason"),
                    "sms_result": sms_outcome.get("sms_result"),
                },
            )
            send_purchase_confirmation_sms.delay(purchase.id)
            sms_status = "queued_retry"

    return {"status": "sent", "purchase_id": purchase.id, "sms_status": sms_status}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_purchase_confirmation_sms(self, purchase_id: int):
    purchase = (
        Purchase.objects.select_related("supplier")
        .filter(pk=purchase_id)
        .first()
    )
    if not purchase:
        return {"status": "missing_purchase"}

    sms_outcome = _send_purchase_confirmation_sms_once(purchase)
    if sms_outcome.get("status") in {"sms_sent", "sms_skipped"}:
        return sms_outcome

    logger.warning(
        "Purchase confirmation SMS failed; scheduling retry",
        extra={
            "purchase_id": purchase.id,
            "reason": sms_outcome.get("reason"),
            "retries": self.request.retries,
            "sms_result": sms_outcome.get("sms_result"),
        },
    )
    retry_delay_seconds = min(300, 60 * (2 ** max(self.request.retries, 0)))
    try:
        raise self.retry(countdown=retry_delay_seconds)
    except MaxRetriesExceededError:
        logger.error(
            "Purchase confirmation SMS retries exhausted",
            extra={
                "purchase_id": purchase.id,
                "reason": sms_outcome.get("reason"),
                "sms_result": sms_outcome.get("sms_result"),
            },
        )
        return {
            "status": "sms_failed",
            "purchase_id": purchase.id,
            "reason": sms_outcome.get("reason") or "max_retries_exceeded",
        }
