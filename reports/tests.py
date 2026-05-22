from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from branches.models import Branch
from products.models import AutoReorderRequest, Product, Purchase, PurchaseItem, Supplier, SupplierReorderRequest
from sales.models import Sale, SaleItem, Shift, ShiftExpense


class ReportsBranchScopeTests(TestCase):
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
        self.super_admin = User.objects.create_user(
            username="superadmin",
            password="pass12345",
            role="super_admin",
            is_staff=True,
            branch=self.wendani,
        )
        self.supplier = Supplier.objects.create(
            name="Dawanol",
            contact_person="Alice",
            phone_number="254700000000",
            email="supplier@example.com",
            address="Nairobi",
            priority=1,
        )

    def test_supplier_report_super_admin_defaults_to_all_branches(self):
        purchase = Purchase.objects.create(
            supplier=self.supplier,
            branch=self.sukari,
            invoice_number="INV-APR15",
            total_amount=Decimal("1200.00"),
            created_by=self.super_admin,
        )
        Purchase.objects.filter(pk=purchase.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 15, 10, 0, 0))
        )

        self.client.force_login(self.super_admin)
        response = self.client.get(
            reverse("supplier-report"),
            {"start_date": "2026-04-15", "end_date": "2026-04-15"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "INV-APR15")
        self.assertContains(response, "Dawanol")

    def test_report_pages_preselect_current_date(self):
        self.client.force_login(self.super_admin)
        today_iso = timezone.localdate().isoformat()

        sales_response = self.client.get(reverse("sales-report"))
        supplier_response = self.client.get(reverse("supplier-report"))
        shift_response = self.client.get(reverse("shift-report"))
        orders_response = self.client.get(reverse("orders-report"))

        self.assertContains(sales_response, f'value="{today_iso}"')
        self.assertContains(supplier_response, f'value="{today_iso}"')
        self.assertContains(shift_response, f'value="{today_iso}"')
        self.assertContains(orders_response, f'value="{today_iso}"')

    def test_orders_report_honors_branch_filter(self):
        product = Product.objects.create(
            name="Orders Report Product",
            barcode="ORD-REPORT-001",
            description="",
            unit_price=Decimal("100.00"),
            cost_price=Decimal("50.00"),
            reorder_level=5,
            max_stock=20,
            pack_quantity=1,
            is_active=True,
        )
        wendani_order = AutoReorderRequest.objects.create(
            product=product,
            target_stock_level=20,
            current_stock_snapshot=2,
            requested_quantity=10,
            remaining_quantity=3,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            branch_requirements={self.wendani.name: 10},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        sukari_order = AutoReorderRequest.objects.create(
            product=product,
            target_stock_level=20,
            current_stock_snapshot=1,
            requested_quantity=8,
            remaining_quantity=0,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={self.sukari.name: 8},
            status=AutoReorderRequest.STATUS_FULFILLED,
        )
        AutoReorderRequest.objects.filter(pk=wendani_order.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 15, 8, 0, 0))
        )
        AutoReorderRequest.objects.filter(pk=sukari_order.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 15, 9, 0, 0))
        )

        self.client.force_login(self.super_admin)
        response = self.client.get(
            reverse("orders-report"),
            {
                "start_date": "2026-04-15",
                "end_date": "2026-04-15",
                "branch_id": str(self.wendani.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"ORD-{wendani_order.id}")
        self.assertNotContains(response, f"ORD-{sukari_order.id}")

    def test_orders_report_keeps_rejected_supplier_orders_out_of_main_tab(self):
        product = Product.objects.create(
            name="Rejected Trail Product",
            barcode="ORD-REJECT-001",
            description="",
            unit_price=Decimal("100.00"),
            cost_price=Decimal("50.00"),
            reorder_level=5,
            max_stock=20,
            pack_quantity=1,
            is_active=True,
        )
        healthy_order = AutoReorderRequest.objects.create(
            product=product,
            target_stock_level=20,
            current_stock_snapshot=2,
            requested_quantity=10,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            branch_requirements={self.wendani.name: 10},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        rejected_order = AutoReorderRequest.objects.create(
            product=product,
            target_stock_level=20,
            current_stock_snapshot=1,
            requested_quantity=8,
            remaining_quantity=8,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={self.wendani.name: 8},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        SupplierReorderRequest.objects.create(
            reorder_request=rejected_order,
            supplier=self.supplier,
            priority=1,
            requested_quantity=8,
            fulfilled_quantity=0,
            status=SupplierReorderRequest.STATUS_REJECTED,
            expires_at=timezone.now() + timedelta(hours=1),
            responded_at=timezone.now(),
        )
        AutoReorderRequest.objects.filter(pk=healthy_order.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 15, 7, 0, 0))
        )
        AutoReorderRequest.objects.filter(pk=rejected_order.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 15, 8, 0, 0))
        )

        self.client.force_login(self.super_admin)
        response = self.client.get(
            reverse("orders-report"),
            {"start_date": "2026-04-15", "end_date": "2026-04-15"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"ORD-{healthy_order.id}")
        self.assertNotContains(response, f"ORD-{rejected_order.id}")

    def test_orders_report_failed_tab_shows_rejected_orders(self):
        product = Product.objects.create(
            name="Rejected Tab Product",
            barcode="ORD-REJECT-002",
            description="",
            unit_price=Decimal("120.00"),
            cost_price=Decimal("70.00"),
            reorder_level=5,
            max_stock=20,
            pack_quantity=1,
            is_active=True,
        )
        rejected_order = AutoReorderRequest.objects.create(
            product=product,
            target_stock_level=20,
            current_stock_snapshot=1,
            requested_quantity=6,
            remaining_quantity=6,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={self.wendani.name: 6},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        SupplierReorderRequest.objects.create(
            reorder_request=rejected_order,
            supplier=self.supplier,
            priority=1,
            requested_quantity=6,
            fulfilled_quantity=0,
            status=SupplierReorderRequest.STATUS_REJECTED,
            expires_at=timezone.now() + timedelta(hours=1),
            responded_at=timezone.now(),
        )
        AutoReorderRequest.objects.filter(pk=rejected_order.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 15, 9, 0, 0))
        )

        self.client.force_login(self.super_admin)
        response = self.client.get(
            reverse("orders-report"),
            {"start_date": "2026-04-15", "end_date": "2026-04-15", "tab": "failed"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Failed Orders")
        self.assertContains(response, f"ORD-{rejected_order.id}")
        self.assertContains(response, 'style="background:#fff1f2;"')

    def test_shift_report_shows_empty_state_when_no_rows(self):
        self.client.force_login(self.super_admin)
        response = self.client.get(
            reverse("shift-report"),
            {"start_date": "2026-04-15", "end_date": "2026-04-15"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No closed shifts found for this period.")

    def test_supplier_invoice_modal_shows_invoice_items(self):
        purchase = Purchase.objects.create(
            supplier=self.supplier,
            branch=self.sukari,
            invoice_number="INV-ITEMS-01",
            total_amount=Decimal("2500.00"),
            created_by=self.super_admin,
        )
        product = Product.objects.create(
            name="Amoxicillin",
            barcode="INV-ITEM-TEST-001",
            description="",
            unit_price=Decimal("100.00"),
            cost_price=Decimal("50.00"),
            reorder_level=5,
            max_stock=20,
            pack_quantity=1,
            is_active=True,
        )
        PurchaseItem.objects.create(
            purchase=purchase,
            product=product,
            quantity=10,
            unit_cost=Decimal("50.00"),
            total_cost=Decimal("500.00"),
        )

        self.client.force_login(self.super_admin)
        response = self.client.get(
            reverse("supplier-invoice-items-modal"),
            {"invoice_id": purchase.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "INV-ITEMS-01")
        self.assertContains(response, "Amoxicillin")
        self.assertContains(response, "KES 500.00")

    def test_dashboard_stats_include_month_expenses_and_gross_profit(self):
        shift = Shift.objects.create(
            cashier=self.super_admin,
            branch=self.wendani,
            opening_cash=Decimal("0.00"),
        )
        ShiftExpense.objects.create(
            shift=shift,
            amount=Decimal("150.00"),
            description="Fuel",
        )
        sale = Sale.objects.create(
            receipt_number="RCP-DASH-001",
            branch=self.wendani,
            cashier=self.super_admin,
            payment_method="cash",
            cash_amount=Decimal("1000.00"),
            mpesa_amount=Decimal("0.00"),
            total_amount=Decimal("1000.00"),
            shift=shift,
        )
        now = timezone.now()
        Shift.objects.filter(pk=shift.pk).update(start_time=now)
        Sale.objects.filter(pk=sale.pk).update(created_at=now)

        self.client.force_login(self.super_admin)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Month Expenses")
        self.assertContains(response, "Month Gross Profit")
        self.assertContains(response, "KES 150")
        self.assertContains(response, "KES 850")

    def test_sales_report_transaction_history_is_super_admin_only(self):
        sale = Sale.objects.create(
            receipt_number="RCP-ORD-001",
            branch=self.wendani,
            cashier=self.super_admin,
            payment_method="cash",
            cash_amount=Decimal("450.00"),
            mpesa_amount=Decimal("0.00"),
            total_amount=Decimal("450.00"),
        )
        Sale.objects.filter(pk=sale.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 15, 9, 0, 0))
        )

        self.client.force_login(self.super_admin)
        admin_response = self.client.get(
            reverse("sales-report"),
            {"start_date": "2026-04-15", "end_date": "2026-04-15", "tab": "transactions"},
        )
        self.assertEqual(admin_response.status_code, 200)
        self.assertContains(admin_response, "Transaction History")
        self.assertContains(admin_response, "RCP-ORD-001")

        cashier = User.objects.create_user(
            username="cashier_orders",
            password="pass12345",
            role="cashier",
            branch=self.wendani,
        )
        self.client.force_login(cashier)
        cashier_response = self.client.get(
            reverse("sales-report"),
            {"start_date": "2026-04-15", "end_date": "2026-04-15", "tab": "transactions"},
        )
        self.assertEqual(cashier_response.status_code, 200)
        self.assertNotContains(cashier_response, "Transaction History")
        self.assertNotContains(cashier_response, "RCP-ORD-001")


class DailySalesBreakdownModalTests(TestCase):
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
        self.super_admin = User.objects.create_user(
            username="superadmin2",
            password="pass12345",
            role="super_admin",
            is_staff=True,
            branch=self.wendani,
        )
        self.product = Product.objects.create(
            name="Paracetamol",
            barcode="RPT-SALE-001",
            description="",
            unit_price=Decimal("100.00"),
            cost_price=Decimal("70.00"),
            reorder_level=10,
            max_stock=100,
            pack_quantity=1,
            is_active=True,
        )

    def _create_sale(self, receipt_number, branch):
        sale = Sale.objects.create(
            receipt_number=receipt_number,
            branch=branch,
            cashier=self.super_admin,
            payment_method="cash",
            cash_amount=Decimal("100.00"),
            mpesa_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=1,
            unit_price=Decimal("100.00"),
            total_price=Decimal("100.00"),
        )
        Sale.objects.filter(pk=sale.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 15, 12, 0, 0))
        )
        return sale

    def test_daily_sales_modal_returns_rows_for_selected_date(self):
        self._create_sale("RCP-WEN-001", self.wendani)
        self._create_sale("RCP-SUK-001", self.sukari)
        self.client.force_login(self.super_admin)

        response = self.client.get(
            reverse("sales-day-breakdown-modal"),
            {"date": "2026-04-15"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RCP-WEN-001")
        self.assertContains(response, "RCP-SUK-001")
        self.assertContains(response, "Paracetamol")

    def test_daily_sales_modal_honors_branch_filter(self):
        self._create_sale("RCP-WEN-002", self.wendani)
        self._create_sale("RCP-SUK-002", self.sukari)
        self.client.force_login(self.super_admin)

        response = self.client.get(
            reverse("sales-day-breakdown-modal"),
            {"date": "2026-04-15", "branch_id": str(self.wendani.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RCP-WEN-002")
        self.assertNotContains(response, "RCP-SUK-002")
