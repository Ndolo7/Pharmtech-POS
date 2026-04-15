from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

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
from products.tasks import scan_low_stock_and_trigger_reorders


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
