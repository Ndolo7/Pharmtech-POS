from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from celery.exceptions import Retry
from django.urls import reverse
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from branches.models import Branch
from products.models import (
    AutoReorderRequest,
    Purchase,
    PurchaseItem,
    Product,
    Stock,
    Supplier,
    SupplierReorderRequest,
)
from products.tasks import (
    scan_low_stock_and_trigger_reorders,
    send_purchase_confirmation_sms,
    send_purchase_confirmation_to_supplier,
)


class AutoReorderScanTests(TestCase):
    def setUp(self):
        self.wendani = Branch.objects.create(
            name="Wendani",
            code="WEN",
            address="Wendani",
            phone_number="0700000001",
            is_active=True,
        )
        self.sukari = Branch.objects.create(
            name="Sukari",
            code="SUK",
            address="Sukari",
            phone_number="0700000002",
            is_active=True,
        )

    @patch("products.tasks.notify_next_supplier.delay")
    def test_scan_uses_ceil_for_packet_calculation(self, _notify_delay):
        product = Product.objects.create(
            name="Small Deficit Item",
            barcode="TEST-CEIL-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=1,
            max_stock=3,
            pack_quantity=20,
            is_active=True,
        )
        Stock.objects.create(product=product, branch=self.wendani, quantity=1)
        Stock.objects.create(product=product, branch=self.sukari, quantity=1)

        scan_low_stock_and_trigger_reorders()

        reorder = AutoReorderRequest.objects.get(product=product)
        self.assertEqual(reorder.requested_quantity, 2)
        self.assertEqual(reorder.branch_requirements, {"Wendani": 1, "Sukari": 1})

    @patch("products.tasks.notify_next_supplier.delay")
    def test_scan_counts_active_branches_without_stock_rows(self, _notify_delay):
        product = Product.objects.create(
            name="Missing Stock Row Item",
            barcode="TEST-BRANCH-001",
            unit_price=Decimal("30.00"),
            cost_price=Decimal("20.00"),
            reorder_level=15,
            max_stock=60,
            pack_quantity=30,
            is_active=True,
        )
        Stock.objects.create(product=product, branch=self.wendani, quantity=3)
        # No stock row for Sukari branch. It should be treated as zero stock.

        scan_low_stock_and_trigger_reorders()

        reorder = AutoReorderRequest.objects.get(product=product)
        self.assertEqual(reorder.requested_quantity, 4)
        self.assertEqual(reorder.branch_requirements, {"Wendani": 2, "Sukari": 2})

    @patch("products.tasks.notify_next_supplier.delay")
    def test_scan_does_not_overwrite_open_manual_reorders(self, _notify_delay):
        product = Product.objects.create(
            name="Manual Protected Item",
            barcode="TEST-MANUAL-001",
            unit_price=Decimal("20.00"),
            cost_price=Decimal("10.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        Stock.objects.create(product=product, branch=self.wendani, quantity=0)
        Stock.objects.create(product=product, branch=self.sukari, quantity=0)

        manual_reorder = AutoReorderRequest.objects.create(
            product=product,
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=2,
            remaining_quantity=2,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            branch_requirements={"Wendani": 2},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        scan_low_stock_and_trigger_reorders()

        manual_reorder.refresh_from_db()
        self.assertEqual(manual_reorder.branch_requirements, {"Wendani": 2})
        self.assertEqual(manual_reorder.requested_quantity, 2)
        self.assertEqual(manual_reorder.origin, AutoReorderRequest.ORIGIN_MANUAL)

        auto_reorder = AutoReorderRequest.objects.get(
            product=product,
            origin=AutoReorderRequest.ORIGIN_AUTO,
        )
        self.assertEqual(auto_reorder.branch_requirements, {"Wendani": 5, "Sukari": 5})


class SupplierReorderResponseViewTests(TestCase):
    def setUp(self):
        self.wendani = Branch.objects.create(
            name="Wendani",
            code="WEN",
            address="Wendani",
            phone_number="0700000001",
            is_active=True,
        )
        self.sukari = Branch.objects.create(
            name="Sukari",
            code="SUK",
            address="Sukari",
            phone_number="0700000002",
            is_active=True,
        )
        self.supplier = Supplier.objects.create(
            name="Main Supplier",
            contact_person="Alice",
            phone_number="254700000000",
            email="supplier@example.com",
            address="Nairobi",
            priority=1,
        )

    def test_supplier_link_shows_all_pending_products_grouped_by_branch(self):
        now = timezone.now()
        first_product = Product.objects.create(
            name="Paracetamol 500mg",
            barcode="TEST-PENDING-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=20,
            max_stock=100,
            pack_quantity=1,
            is_active=True,
        )
        second_product = Product.objects.create(
            name="MGLOZART",
            barcode="TEST-PENDING-002",
            unit_price=Decimal("20.00"),
            cost_price=Decimal("10.00"),
            reorder_level=15,
            max_stock=60,
            pack_quantity=30,
            is_active=True,
        )

        first_reorder = AutoReorderRequest.objects.create(
            product=first_product,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=120,
            remaining_quantity=120,
            branch_requirements={"Wendani": 80, "Sukari": 40},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        second_reorder = AutoReorderRequest.objects.create(
            product=second_product,
            target_stock_level=60,
            current_stock_snapshot=3,
            requested_quantity=4,
            remaining_quantity=4,
            branch_requirements={"Wendani": 2, "Sukari": 2},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        first_request = SupplierReorderRequest.objects.create(
            reorder_request=first_reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=120,
            expires_at=now + timedelta(hours=1),
        )
        SupplierReorderRequest.objects.create(
            reorder_request=second_reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=4,
            expires_at=now + timedelta(hours=1),
        )

        response = self.client.get(
            reverse("supplier-reorder-response", kwargs={"token": first_request.token})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paracetamol 500mg")
        self.assertContains(response, "MGLOZART")
        self.assertContains(response, "Wendani")
        self.assertContains(response, "Sukari")
        self.assertContains(response, "All needed products grouped by branch")

    @patch("products.views.notify_next_supplier.delay")
    def test_yes_response_without_quantity_defaults_to_requested_quantity(self, _notify_delay):
        now = timezone.now()
        product = Product.objects.create(
            name="Manual Confirm Item",
            barcode="TEST-CONFIRM-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=10,
            max_stock=100,
            pack_quantity=1,
            is_active=True,
        )
        reorder = AutoReorderRequest.objects.create(
            product=product,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=8,
            remaining_quantity=8,
            branch_requirements={"Wendani": 8},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        supplier_request = SupplierReorderRequest.objects.create(
            reorder_request=reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=8,
            expires_at=now + timedelta(hours=1),
        )

        response = self.client.post(
            reverse("supplier-reorder-response", kwargs={"token": supplier_request.token}),
            data={
                "request_id": [str(supplier_request.id)],
                f"can_supply_{supplier_request.id}": "yes",
            },
        )

        self.assertEqual(response.status_code, 200)
        supplier_request.refresh_from_db()
        reorder.refresh_from_db()
        self.assertEqual(supplier_request.status, SupplierReorderRequest.STATUS_ACCEPTED)
        self.assertEqual(supplier_request.fulfilled_quantity, 8)
        self.assertEqual(reorder.remaining_quantity, 0)
        self.assertEqual(reorder.status, AutoReorderRequest.STATUS_FULFILLED)


class ManualOrderCreateViewTests(TestCase):
    def setUp(self):
        self.branch_a = Branch.objects.create(
            name="Wendani",
            code="WEN",
            address="Wendani",
            phone_number="0700000001",
            is_active=True,
        )
        self.branch_b = Branch.objects.create(
            name="Sukari",
            code="SUK",
            address="Sukari",
            phone_number="0700000002",
            is_active=True,
        )
        self.admin_user = User.objects.create_user(
            username="superadmin",
            password="pass12345",
            role="super_admin",
            branch=self.branch_a,
        )
        self.client.force_login(self.admin_user)

        self.product_one = Product.objects.create(
            name="Product One",
            barcode="MANUAL-P1",
            unit_price=Decimal("50.00"),
            cost_price=Decimal("30.00"),
            reorder_level=10,
            max_stock=100,
            pack_quantity=10,
            is_active=True,
        )
        self.product_two = Product.objects.create(
            name="Product Two",
            barcode="MANUAL-P2",
            unit_price=Decimal("40.00"),
            cost_price=Decimal("20.00"),
            reorder_level=10,
            max_stock=60,
            pack_quantity=10,
            is_active=True,
        )
        self.product_limited = Product.objects.create(
            name="Limited Product",
            barcode="MANUAL-LIMIT-1",
            unit_price=Decimal("20.00"),
            cost_price=Decimal("10.00"),
            reorder_level=1,
            max_stock=5,
            pack_quantity=1,
            is_active=True,
        )

    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_supports_multiple_products_and_branches(self, notify_delay):
        existing_manual = AutoReorderRequest.objects.create(
            product=self.product_one,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=1,
            remaining_quantity=1,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            branch_requirements={self.branch_a.name: 1},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        response = self.client.post(
            reverse("create-order"),
            data={
                "product_id[]": [str(self.product_one.id), str(self.product_one.id), str(self.product_two.id)],
                "branch_id[]": [str(self.branch_a.id), str(self.branch_b.id), str(self.branch_b.id)],
                "packets[]": ["3", "2", "4"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        existing_manual.refresh_from_db()
        self.assertEqual(existing_manual.requested_quantity, 6)
        self.assertEqual(existing_manual.remaining_quantity, 6)
        self.assertEqual(existing_manual.branch_requirements, {"Wendani": 4, "Sukari": 2})

        second_reorder = AutoReorderRequest.objects.get(
            product=self.product_two,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
        )
        self.assertEqual(second_reorder.requested_quantity, 4)
        self.assertEqual(second_reorder.branch_requirements, {"Sukari": 4})

        notified_ids = {call.args[0] for call in notify_delay.call_args_list}
        self.assertEqual(notified_ids, {existing_manual.id, second_reorder.id})

    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_rejects_packets_above_product_max(self, notify_delay):
        response = self.client.post(
            reverse("create-order"),
            data={
                "product_id[]": [str(self.product_two.id)],
                "branch_id[]": [str(self.branch_a.id)],
                "packets[]": ["7"],  # Product Two max packets is ceil(60/10) = 6
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(AutoReorderRequest.objects.filter(origin=AutoReorderRequest.ORIGIN_MANUAL).count(), 0)
        notify_delay.assert_not_called()

    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_rejects_when_projected_stock_exceeds_max(self, notify_delay):
        Stock.objects.create(product=self.product_limited, branch=self.branch_a, quantity=3)

        response = self.client.post(
            reverse("create-order"),
            data={
                "product_id[]": [str(self.product_limited.id)],
                "branch_id[]": [str(self.branch_a.id)],
                "packets[]": ["4"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "can only accept", status_code=400)
        notify_delay.assert_not_called()


class ReceiveStockPricingValidationTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name="Wendani",
            code="WEN",
            address="Wendani",
            phone_number="0700000001",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="receiver",
            password="pass12345",
            role="super_admin",
            branch=self.branch,
        )
        self.supplier = Supplier.objects.create(
            name="SupplyCo",
            contact_person="Alice",
            phone_number="254700000000",
            email="supplier@example.com",
            address="Nairobi",
            priority=1,
        )
        self.product = Product.objects.create(
            name="Painkiller",
            barcode="RCV-PRICE-001",
            unit_price=Decimal("150.00"),
            cost_price=Decimal("100.00"),
            reorder_level=10,
            max_stock=100,
            pack_quantity=10,
            is_active=True,
        )
        self.reorder = AutoReorderRequest.objects.create(
            product=self.product,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=10,
            remaining_quantity=10,
            branch_requirements={self.branch.name: 10},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        self.supplier_request = SupplierReorderRequest.objects.create(
            reorder_request=self.reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=10,
            fulfilled_quantity=10,
            received_quantity=0,
            status=SupplierReorderRequest.STATUS_ACCEPTED,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client.force_login(self.user)

    def test_receive_stock_rejects_price_below_33_percent_margin(self):
        response = self.client.post(
            reverse("receive-stock"),
            data={
                "supplier_id": str(self.supplier.id),
                "invoice_number": "INV-LOW-MARGIN",
                "reorder_request_id[]": [str(self.supplier_request.id)],
                "quantity[]": ["5"],
                "cost_price[]": ["100.00"],
                "selling_price[]": ["12.00"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must be at least")
        self.assertEqual(Purchase.objects.count(), 0)

    def test_receive_stock_accepts_valid_margin_and_updates_product_prices(self):
        response = self.client.post(
            reverse("receive-stock"),
            data={
                "supplier_id": str(self.supplier.id),
                "invoice_number": "INV-VALID-MARGIN",
                "reorder_request_id[]": [str(self.supplier_request.id)],
                "quantity[]": ["5"],
                "cost_price[]": ["90.00"],
                "selling_price[]": ["120.00"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Purchase.objects.count(), 1)
        purchase = Purchase.objects.get()
        self.assertEqual(purchase.total_amount, Decimal("450.00"))
        purchase_item = PurchaseItem.objects.get()
        self.assertEqual(purchase_item.unit_cost, Decimal("9.00"))
        self.assertEqual(purchase_item.quantity, 50)

        self.product.refresh_from_db()
        self.assertEqual(self.product.cost_price, Decimal("90.00"))
        self.assertEqual(self.product.unit_price, Decimal("120.00"))

    @patch("products.views.send_purchase_confirmation_to_supplier.delay")
    def test_receive_stock_queues_supplier_pdf_confirmation(self, queue_pdf_task):
        response = self.client.post(
            reverse("receive-stock"),
            data={
                "supplier_id": str(self.supplier.id),
                "invoice_number": "INV-CONFIRM-PDF",
                "reorder_request_id[]": [str(self.supplier_request.id)],
                "quantity[]": ["2"],
                "cost_price[]": ["90.00"],
                "selling_price[]": ["120.00"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Purchase.objects.count(), 1)
        purchase = Purchase.objects.get()
        queue_pdf_task.assert_called_once_with(purchase.id)


class ReceiveStockQuantityPermissionTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name="Wendani",
            code="WENQ",
            address="Wendani",
            phone_number="0700000011",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="receiver_pharmtec",
            password="pass12345",
            role="pharmtec",
            branch=self.branch,
        )
        self.supplier = Supplier.objects.create(
            name="SupplyCo Qty",
            contact_person="Alice",
            phone_number="254700000010",
            email="supplier-qty@example.com",
            address="Nairobi",
            priority=1,
        )
        self.product = Product.objects.create(
            name="Qty Protected",
            barcode="RCV-QTY-001",
            unit_price=Decimal("150.00"),
            cost_price=Decimal("100.00"),
            reorder_level=10,
            max_stock=100,
            pack_quantity=10,
            is_active=True,
        )
        self.reorder = AutoReorderRequest.objects.create(
            product=self.product,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=10,
            remaining_quantity=10,
            branch_requirements={self.branch.name: 10},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        self.supplier_request = SupplierReorderRequest.objects.create(
            reorder_request=self.reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=10,
            fulfilled_quantity=10,
            received_quantity=0,
            status=SupplierReorderRequest.STATUS_ACCEPTED,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client.force_login(self.user)

    def test_non_super_admin_cannot_edit_receive_quantity(self):
        response = self.client.post(
            reverse("receive-stock"),
            data={
                "supplier_id": str(self.supplier.id),
                "invoice_number": "INV-QTY-OVERRIDE",
                "reorder_request_id[]": [str(self.supplier_request.id)],
                "quantity[]": ["5"],
                "cost_price[]": ["90.00"],
                "selling_price[]": ["120.00"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "only be edited by super admin")
        self.assertEqual(Purchase.objects.count(), 0)

class PurchaseConfirmationTaskTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name="Wendani",
            code="WEN",
            address="Wendani",
            phone_number="0700000001",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="receiver_task",
            password="pass12345",
            role="pharmtec",
            branch=self.branch,
        )
        self.supplier = Supplier.objects.create(
            name="SupplyCo",
            contact_person="Alice",
            phone_number="254700000000",
            email="supplier@example.com",
            address="Nairobi",
            priority=1,
        )
        self.product = Product.objects.create(
            name="Task Product",
            barcode="TASK-PROD-001",
            unit_price=Decimal("110.00"),
            cost_price=Decimal("80.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        self.purchase = Purchase.objects.create(
            supplier=self.supplier,
            branch=self.branch,
            invoice_number="INV-TASK-001",
            total_amount=Decimal("240.00"),
            created_by=self.user,
        )
        PurchaseItem.objects.create(
            purchase=self.purchase,
            product=self.product,
            quantity=20,
            unit_cost=Decimal("12.00"),
        )

    @patch("products.tasks.send_purchase_confirmation_sms.delay")
    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.EmailMessage")
    def test_send_purchase_confirmation_attaches_pdf_and_sends_sms_immediately(
        self, email_cls, sms_send, queue_sms_task
    ):
        email_instance = email_cls.return_value
        sms_send.return_value = {"success": True, "response": {"ok": True}}
        result = send_purchase_confirmation_to_supplier(self.purchase.id)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["sms_status"], "sms_sent")
        email_cls.assert_called_once()
        self.assertTrue(email_instance.attach.called)
        attachment_name = email_instance.attach.call_args.args[0]
        attachment_bytes = email_instance.attach.call_args.args[1]
        self.assertTrue(attachment_name.endswith(".pdf"))
        self.assertGreater(len(attachment_bytes), 0)
        email_instance.send.assert_called_once_with(fail_silently=False)
        sms_send.assert_called_once()
        queue_sms_task.assert_not_called()

    @patch("products.tasks.send_purchase_confirmation_sms.delay")
    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.EmailMessage")
    def test_send_purchase_confirmation_queues_retry_when_immediate_sms_fails(
        self, email_cls, sms_send, queue_sms_task
    ):
        sms_send.return_value = {"success": False, "reason": "request_error"}
        result = send_purchase_confirmation_to_supplier(self.purchase.id)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["sms_status"], "queued_retry")
        email_cls.assert_called_once()
        queue_sms_task.assert_called_once_with(self.purchase.id)

    @patch("products.tasks.send_sms_via_leopard")
    def test_send_purchase_confirmation_sms_sends_to_supplier(self, sms_send):
        sms_send.return_value = {"success": True, "response": {"ok": True}}
        result = send_purchase_confirmation_sms(self.purchase.id)

        self.assertEqual(result["status"], "sms_sent")
        sms_send.assert_called_once()

    @patch("products.tasks.send_sms_via_leopard")
    def test_send_purchase_confirmation_sms_retries_on_request_error(self, sms_send):
        sms_send.return_value = {"success": False, "reason": "request_error"}
        with self.assertRaises(Retry):
            send_purchase_confirmation_sms(self.purchase.id)

    @patch("products.tasks.send_sms_via_leopard")
    def test_send_purchase_confirmation_sms_skips_when_not_configured(self, sms_send):
        sms_send.return_value = {"success": False, "reason": "not_configured"}
        result = send_purchase_confirmation_sms(self.purchase.id)

        self.assertEqual(result["status"], "sms_skipped")
        self.assertEqual(result["reason"], "not_configured")
