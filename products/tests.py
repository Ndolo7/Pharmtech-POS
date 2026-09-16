from decimal import Decimal
from datetime import time as dt_time, timedelta
from unittest.mock import patch

from celery.exceptions import MaxRetriesExceededError, Retry
from django.conf import settings
from django.urls import reverse
from django.test import TestCase, override_settings
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

from accounts.models import User
from branches.models import Branch
from products.models import (
    AutoOrderScheduleSetting,
    AutoReorderRequest,
    BranchSupplyRequest,
    Purchase,
    PurchaseItem,
    Product,
    Stock,
    StockMovement,
    Supplier,
    SupplierReorderRequest,
)
from products.scheduling import (
    AUTO_ORDER_PERIODIC_TASK_NAME,
    AUTO_ORDER_SUNDAY_PERIODIC_TASK_NAME,
    sync_auto_order_periodic_task,
)
from products.tasks import (
    notify_next_supplier,
    scan_low_stock_and_trigger_reorders,
    send_supplier_reorder_sms,
    send_batched_exhaustion_alerts,
    send_purchase_confirmation_sms,
    send_purchase_confirmation_to_supplier,
)
from sales.models import Sale, SaleItem


@override_settings(AUTO_ORDER_ENFORCE_APPOINTED_WINDOW=False)
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
        self.cashier = User.objects.create_user(
            username="scan_cashier",
            password="pass12345",
            role="cashier",
            branch=self.wendani,
        )
        self._receipt_counter = 0

    def _mark_product_as_sold(self, product, branch=None, quantity=1, days_ago=1):
        branch = branch or self.wendani
        self._receipt_counter += 1
        sale = Sale.objects.create(
            receipt_number=f"SCAN-SALE-{self._receipt_counter:03d}",
            branch=branch,
            cashier=self.cashier,
            payment_method="cash",
            cash_amount=Decimal("0.00"),
            mpesa_amount=Decimal("0.00"),
            credit_amount=Decimal("0.00"),
            total_amount=Decimal(product.unit_price) * quantity,
        )
        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=quantity,
            unit_price=product.unit_price,
            total_price=Decimal(product.unit_price) * quantity,
        )
        sale_time = timezone.now() - timedelta(days=max(int(days_ago), 0))
        Sale.objects.filter(pk=sale.pk).update(created_at=sale_time)

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
        self._mark_product_as_sold(product, branch=self.wendani, quantity=1)

        scan_low_stock_and_trigger_reorders()

        reorder = AutoReorderRequest.objects.get(product=product)
        self.assertEqual(reorder.requested_quantity, 1)
        self.assertEqual(reorder.branch_requirements, {"Wendani": 1})

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
        self._mark_product_as_sold(product, branch=self.wendani, quantity=1)

        scan_low_stock_and_trigger_reorders()

        reorder = AutoReorderRequest.objects.get(product=product)
        self.assertEqual(reorder.requested_quantity, 2)
        self.assertEqual(reorder.branch_requirements, {"Wendani": 2})

    @patch("products.tasks.notify_next_supplier.delay")
    def test_scan_pack_quantity_above_one_orders_full_max_stock_quantity(self, _notify_delay):
        product = Product.objects.create(
            name="Pack Governed Item",
            barcode="TEST-PACK-RULE-001",
            unit_price=Decimal("30.00"),
            cost_price=Decimal("18.00"),
            reorder_level=10,
            max_stock=100,
            pack_quantity=28,
            is_active=True,
        )
        Stock.objects.create(product=product, branch=self.wendani, quantity=10)
        self._mark_product_as_sold(product, branch=self.wendani, quantity=1)

        scan_low_stock_and_trigger_reorders()

        reorder = AutoReorderRequest.objects.get(product=product)
        self.assertEqual(reorder.requested_quantity, 4)
        self.assertEqual(reorder.branch_requirements, {"Wendani": 4})

    @patch("products.tasks.notify_next_supplier.delay")
    def test_scan_pack_quantity_one_orders_deficit_only(self, _notify_delay):
        product = Product.objects.create(
            name="Single Unit Pack Item",
            barcode="TEST-PACK-RULE-002",
            unit_price=Decimal("30.00"),
            cost_price=Decimal("18.00"),
            reorder_level=2,
            max_stock=5,
            pack_quantity=1,
            is_active=True,
        )
        Stock.objects.create(product=product, branch=self.wendani, quantity=2)
        self._mark_product_as_sold(product, branch=self.wendani, quantity=1)

        scan_low_stock_and_trigger_reorders()

        reorder = AutoReorderRequest.objects.get(product=product)
        self.assertEqual(reorder.requested_quantity, 3)
        self.assertEqual(reorder.branch_requirements, {"Wendani": 3})

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
        self._mark_product_as_sold(product, branch=self.sukari, quantity=1)

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
        self.assertEqual(auto_reorder.branch_requirements, {"Sukari": 5})

    @patch("products.tasks.notify_next_supplier.delay")
    def test_scan_skips_unsold_products_and_cancels_open_unsold_auto_reorders(self, notify_delay):
        unsold_product = Product.objects.create(
            name="Unsold Product",
            barcode="TEST-UNSOLD-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        sold_product = Product.objects.create(
            name="Sold Product",
            barcode="TEST-SOLD-001",
            unit_price=Decimal("20.00"),
            cost_price=Decimal("10.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        Stock.objects.create(product=unsold_product, branch=self.wendani, quantity=0)
        Stock.objects.create(product=sold_product, branch=self.wendani, quantity=0)
        self._mark_product_as_sold(sold_product, branch=self.wendani, quantity=1)

        stale_unsold_reorder = AutoReorderRequest.objects.create(
            product=unsold_product,
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={self.wendani.name: 5},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        result = scan_low_stock_and_trigger_reorders()

        unsold_product.refresh_from_db()
        stale_unsold_reorder.refresh_from_db()
        # Staleness is now tracked per branch on the Stock row, not globally on the product.
        unsold_stock = Stock.objects.get(product=unsold_product, branch=self.wendani)
        self.assertFalse(unsold_stock.exempt_from_auto_reorder)
        self.assertEqual(stale_unsold_reorder.status, AutoReorderRequest.STATUS_CANCELLED)
        self.assertIsNotNone(stale_unsold_reorder.completed_at)
        self.assertFalse(
            AutoReorderRequest.objects.filter(
                product=unsold_product,
                origin=AutoReorderRequest.ORIGIN_AUTO,
                status=AutoReorderRequest.STATUS_OPEN,
            ).exists()
        )
        self.assertTrue(
            AutoReorderRequest.objects.filter(
                product=sold_product,
                origin=AutoReorderRequest.ORIGIN_AUTO,
                status=AutoReorderRequest.STATUS_OPEN,
            ).exists()
        )
        sold_reorder = AutoReorderRequest.objects.get(
            product=sold_product,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            status=AutoReorderRequest.STATUS_OPEN,
        )
        self.assertEqual(sold_reorder.branch_requirements, {"Wendani": 5})
        self.assertGreaterEqual(result.get("cancelled_unsold", 0), 1)
        notify_delay.assert_called_once()

    @patch("products.tasks.notify_next_supplier.delay")
    def test_scan_marks_products_unsold_for_75_days_exempt(self, notify_delay):
        stale_product = Product.objects.create(
            name="Stale Product",
            barcode="TEST-STALE-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        recent_product = Product.objects.create(
            name="Recent Product",
            barcode="TEST-RECENT-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        # Stock rows must exist for the per-branch exempt scan to update them.
        Stock.objects.create(product=stale_product, branch=self.wendani, quantity=10)
        Stock.objects.create(product=recent_product, branch=self.wendani, quantity=10)
        self._mark_product_as_sold(stale_product, branch=self.wendani, quantity=1, days_ago=76)
        self._mark_product_as_sold(recent_product, branch=self.wendani, quantity=1, days_ago=74)

        scan_low_stock_and_trigger_reorders()

        # Staleness is now per-branch on Stock, not per-product.
        stale_stock = Stock.objects.get(product=stale_product, branch=self.wendani)
        recent_stock = Stock.objects.get(product=recent_product, branch=self.wendani)
        self.assertTrue(stale_stock.exempt_from_auto_reorder)
        self.assertFalse(recent_stock.exempt_from_auto_reorder)
        notify_delay.assert_not_called()

    @patch("products.tasks.notify_next_supplier.delay")
    def test_scan_does_not_exempt_zero_stock_or_unstocked_products(self, notify_delay):
        unstocked_product = Product.objects.create(
            name="Unstocked Zero Stock Item",
            barcode="TEST-ZERO-STOCK-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        positive_stock_product = Product.objects.create(
            name="Positive Stock Stale Item",
            barcode="TEST-POS-STOCK-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        # Unstocked item starts at 0 stock
        unstocked = Stock.objects.create(product=unstocked_product, branch=self.wendani, quantity=0)
        # Positive stock item sits for 80 days
        deadstock = Stock.objects.create(product=positive_stock_product, branch=self.wendani, quantity=10)
        self._mark_product_as_sold(unstocked_product, branch=self.wendani, quantity=1, days_ago=80)
        self._mark_product_as_sold(positive_stock_product, branch=self.wendani, quantity=1, days_ago=80)

        scan_low_stock_and_trigger_reorders()

        unstocked.refresh_from_db()
        deadstock.refresh_from_db()
        # Zero stock item is NOT auto-exempted
        self.assertFalse(unstocked.exempt_from_auto_reorder)
        # In-stock deadstock item IS auto-exempted
        self.assertTrue(deadstock.exempt_from_auto_reorder)

        # Now simulate deadstock being transferred/sold down to 0 stock
        deadstock.quantity = 0
        deadstock.save(update_fields=["quantity", "updated_at"])

        scan_low_stock_and_trigger_reorders()

        deadstock.refresh_from_db()
        # Exemption remains True so deadstock is NOT automatically reordered
        self.assertTrue(deadstock.exempt_from_auto_reorder)

    def test_migration_fixes_only_never_stocked_zero_quantity_rows(self):
        import importlib
        migration_module = importlib.import_module("products.migrations.0021_clear_zero_stock_auto_exemption")
        fix_wrongly_exempted_unstocked_products = migration_module.fix_wrongly_exempted_unstocked_products
        from products.models import StockMovement, PurchaseItem, TransferItem

        never_stocked_product = Product.objects.create(
            name="Never Stocked Item",
            barcode="MIG-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        was_stocked_product = Product.objects.create(
            name="Was Stocked Item",
            barcode="MIG-002",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )

        never_stocked_row = Stock.objects.create(product=never_stocked_product, branch=self.wendani, quantity=0, exempt_from_auto_reorder=True)
        was_stocked_row = Stock.objects.create(product=was_stocked_product, branch=self.wendani, quantity=0, exempt_from_auto_reorder=True)

        # Create historical movement for was_stocked_row
        StockMovement.objects.create(
            product=was_stocked_product,
            branch=self.wendani,
            movement_type="purchase",
            quantity=20,
            created_by=self.cashier,
        )

        class MockApps:
            @staticmethod
            def get_model(app_name, model_name):
                mapping = {
                    "Stock": Stock,
                    "StockMovement": StockMovement,
                    "PurchaseItem": PurchaseItem,
                    "TransferItem": TransferItem,
                }
                return mapping[model_name]

        fix_wrongly_exempted_unstocked_products(MockApps(), None)

        never_stocked_row.refresh_from_db()
        was_stocked_row.refresh_from_db()

        # Never stocked item is un-exempted (FIXED)
        self.assertFalse(never_stocked_row.exempt_from_auto_reorder)
        # Item that was previously stocked stays exempted (PRESERVED)
        self.assertTrue(was_stocked_row.exempt_from_auto_reorder)


    @patch("products.tasks.notify_next_supplier.delay")
    @patch("products.tasks.send_sms_via_leopard")
    def test_scan_sends_sms_to_stale_source_branch_for_internal_supply(self, send_sms_mock, notify_delay):
        send_sms_mock.return_value = {"success": True, "response": {"ok": True}}
        product = Product.objects.create(
            name="Branch Supplied Item",
            barcode="TEST-BRANCH-SMS-001",
            unit_price=Decimal("30.00"),
            cost_price=Decimal("18.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        Stock.objects.create(product=product, branch=self.wendani, quantity=0)
        Stock.objects.create(product=product, branch=self.sukari, quantity=40)
        self._mark_product_as_sold(product, branch=self.wendani, quantity=1, days_ago=1)
        self._mark_product_as_sold(product, branch=self.sukari, quantity=1, days_ago=76)

        result = scan_low_stock_and_trigger_reorders()

        branch_request = BranchSupplyRequest.objects.get(product=product)
        self.assertEqual(branch_request.source_branch, self.sukari)
        self.assertEqual(branch_request.destination_branch, self.wendani)
        self.assertEqual(branch_request.requested_quantity, 5)
        self.assertEqual(result.get("branch_supply_requests"), 1)
        send_sms_mock.assert_called_once()
        sms_kwargs = send_sms_mock.call_args.kwargs
        self.assertEqual(sms_kwargs["destinations"], self.sukari.phone_number)
        self.assertIn(product.name, sms_kwargs["message"])
        self.assertIn(self.wendani.name, sms_kwargs["message"])
        notify_delay.assert_not_called()

    @patch("products.tasks.notify_next_supplier.delay")
    def test_exempt_branch_stays_exempt_even_when_it_sells(self, notify_delay):
        """Stock.exempt_from_auto_reorder persists until manually cleared via the UI.
        A branch that is stale-exempt will not trigger a reorder even if it sells the
        product yesterday — the flag is only cleared by an admin via the adjust form."""
        product = Product.objects.create(
            name="Persistently Exempt Branch Item",
            barcode="TEST-EXEMPT-STAYS-001",
            unit_price=Decimal("15.00"),
            cost_price=Decimal("8.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        # Wendani's stock is stale-exempt (marked by a previous scan cycle).
        wendani_stock = Stock.objects.create(
            product=product, branch=self.wendani, quantity=0, exempt_from_auto_reorder=True
        )
        # Sukari also stale-exempt — should remain so regardless of what Wendani does.
        sukari_stock = Stock.objects.create(
            product=product, branch=self.sukari, quantity=20, exempt_from_auto_reorder=True
        )
        # Wendani sells the product yesterday.
        self._mark_product_as_sold(product, branch=self.wendani, quantity=1, days_ago=1)

        scan_low_stock_and_trigger_reorders()

        wendani_stock.refresh_from_db()
        sukari_stock.refresh_from_db()
        # The sale does NOT clear the exempt flag — it persists until manually edited.
        self.assertTrue(wendani_stock.exempt_from_auto_reorder)
        self.assertTrue(sukari_stock.exempt_from_auto_reorder)
        # No reorder created because Wendani is exempt.
        self.assertFalse(AutoReorderRequest.objects.filter(product=product).exists())
        notify_delay.assert_not_called()

    @patch("products.tasks.notify_next_supplier.delay")
    def test_manual_product_exempt_blocks_all_branches_regardless_of_sales(self, notify_delay):
        """Product.exempt_from_auto_reorder is a manual administrator override that
        suppresses auto-reorder for ALL branches even when the product is selling."""
        product = Product.objects.create(
            name="Manually Exempt Product",
            barcode="TEST-MANUAL-EXEMPT-001",
            unit_price=Decimal("15.00"),
            cost_price=Decimal("8.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            exempt_from_auto_reorder=True,  # manual global override
            is_active=True,
        )
        Stock.objects.create(product=product, branch=self.wendani, quantity=0)
        self._mark_product_as_sold(product, branch=self.wendani, quantity=1, days_ago=1)

        scan_low_stock_and_trigger_reorders()

        product.refresh_from_db()
        # The scan must NOT clear the manual product-level exempt flag.
        self.assertTrue(product.exempt_from_auto_reorder)
        # No reorder should be created.
        self.assertFalse(AutoReorderRequest.objects.filter(product=product).exists())
        notify_delay.assert_not_called()

    @patch("products.tasks.notify_next_supplier.delay")
    def test_scan_only_considers_products_sold_yesterday(self, notify_delay):
        product = Product.objects.create(
            name="Not Sold Yesterday Item",
            barcode="TEST-YESTERDAY-001",
            unit_price=Decimal("15.00"),
            cost_price=Decimal("7.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        Stock.objects.create(product=product, branch=self.wendani, quantity=0)
        self._mark_product_as_sold(product, branch=self.wendani, quantity=1, days_ago=2)

        result = scan_low_stock_and_trigger_reorders()

        self.assertEqual(result.get("created"), 0)
        self.assertFalse(
            AutoReorderRequest.objects.filter(
                product=product,
                origin=AutoReorderRequest.ORIGIN_AUTO,
                status=AutoReorderRequest.STATUS_OPEN,
            ).exists()
        )
        notify_delay.assert_not_called()


class AutoOrderScheduleSyncTests(TestCase):
    def setUp(self):
        AutoOrderScheduleSetting.objects.all().delete()

    def test_sync_uses_daily_crontab_even_when_legacy_flag_is_disabled(self):
        setting = AutoOrderScheduleSetting.objects.create(
            use_daily_run_time=False,
            daily_run_time=dt_time(8, 0),
            sunday_run_time=dt_time(11, 0),
        )

        periodic_task = sync_auto_order_periodic_task(setting)
        periodic_task.refresh_from_db()

        self.assertEqual(periodic_task.name, AUTO_ORDER_PERIODIC_TASK_NAME)
        self.assertIsNotNone(periodic_task.crontab)
        self.assertIsNone(periodic_task.interval)
        self.assertEqual(periodic_task.crontab.hour, "8")
        self.assertEqual(periodic_task.crontab.minute, "0")
        self.assertEqual(str(periodic_task.crontab.timezone), settings.TIME_ZONE)
        sunday_task = PeriodicTask.objects.get(name=AUTO_ORDER_SUNDAY_PERIODIC_TASK_NAME)
        self.assertIsNotNone(sunday_task.crontab)
        self.assertEqual(sunday_task.crontab.hour, "11")
        self.assertEqual(sunday_task.crontab.minute, "0")
        self.assertEqual(sunday_task.crontab.day_of_week, "0")

    def test_sync_uses_daily_crontab_when_daily_override_is_enabled(self):
        setting = AutoOrderScheduleSetting.objects.create(
            use_daily_run_time=True,
            daily_run_time=dt_time(8, 0),
            sunday_run_time=dt_time(9, 30),
        )

        periodic_task = sync_auto_order_periodic_task(setting)
        periodic_task.refresh_from_db()

        self.assertIsNotNone(periodic_task.crontab)
        self.assertIsNone(periodic_task.interval)
        self.assertEqual(periodic_task.crontab.hour, "8")
        self.assertEqual(periodic_task.crontab.minute, "0")
        self.assertEqual(str(periodic_task.crontab.timezone), settings.TIME_ZONE)
        self.assertTrue(
            PeriodicTask.objects.filter(name=AUTO_ORDER_PERIODIC_TASK_NAME, task="products.tasks.scan_low_stock_and_trigger_reorders").exists()
        )
        sunday_task = PeriodicTask.objects.get(name=AUTO_ORDER_SUNDAY_PERIODIC_TASK_NAME)
        self.assertEqual(sunday_task.crontab.hour, "9")
        self.assertEqual(sunday_task.crontab.minute, "30")
        self.assertEqual(sunday_task.crontab.day_of_week, "0")


@override_settings(AUTO_ORDER_ENFORCE_APPOINTED_WINDOW=True, AUTO_ORDER_SCAN_WINDOW_MINUTES=10)
class AutoReorderScheduleWindowGuardTests(TestCase):
    @patch("products.tasks.notify_next_supplier.delay")
    @patch("products.tasks._is_appointed_scan_time", return_value=False)
    def test_scan_skips_when_outside_appointed_window(self, _appointed_time_mock, notify_delay):
        branch = Branch.objects.create(
            name="Guard Branch",
            code="GBR",
            address="Guard",
            phone_number="0700000099",
            is_active=True,
        )
        product = Product.objects.create(
            name="Guard Product",
            barcode="GUARD-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("5.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        cashier = User.objects.create_user(
            username="guard_cashier",
            password="pass12345",
            role="cashier",
            branch=branch,
        )
        sale = Sale.objects.create(
            receipt_number="GUARD-SALE-001",
            branch=branch,
            cashier=cashier,
            payment_method="cash",
            cash_amount=Decimal("10.00"),
            mpesa_amount=Decimal("0.00"),
            credit_amount=Decimal("0.00"),
            total_amount=Decimal("10.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=1,
            unit_price=Decimal("10.00"),
            total_price=Decimal("10.00"),
        )
        Stock.objects.create(product=product, branch=branch, quantity=0)

        result = scan_low_stock_and_trigger_reorders()

        self.assertEqual(result["status"], "skipped_outside_schedule")
        self.assertFalse(AutoReorderRequest.objects.exists())
        notify_delay.assert_not_called()


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

    def _build_branch_response_payload(self, supplier_request, decisions):
        branch_requirements = supplier_request.reorder_request.branch_requirements or {}
        payload = {"row_ref": []}

        for idx, (branch_name, can_supply, quantity) in enumerate(decisions):
            row_ref = f"row_{idx}"
            requested_qty = int(branch_requirements.get(branch_name, 0) or 0)
            requested_qty = max(requested_qty, 0)

            payload["row_ref"].append(row_ref)
            payload[f"request_id_{row_ref}"] = str(supplier_request.id)
            payload[f"branch_name_{row_ref}"] = branch_name
            payload[f"branch_requested_{row_ref}"] = str(requested_qty)
            payload[f"can_supply_{row_ref}"] = "yes" if can_supply else "no"
            if quantity is not None:
                payload[f"quantity_{row_ref}"] = str(quantity)

        return payload

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

    def test_response_form_keeps_branch_layout_with_unique_controls(self):
        now = timezone.now()
        product = Product.objects.create(
            name="Branch Split Item",
            barcode="TEST-PENDING-UNIQUE-001",
            unit_price=Decimal("14.00"),
            cost_price=Decimal("7.00"),
            reorder_level=10,
            max_stock=100,
            pack_quantity=1,
            is_active=True,
        )
        reorder = AutoReorderRequest.objects.create(
            product=product,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=10,
            remaining_quantity=10,
            branch_requirements={"Wendani": 6, "Sukari": 4},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        supplier_request = SupplierReorderRequest.objects.create(
            reorder_request=reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=10,
            expires_at=now + timedelta(hours=1),
        )

        response = self.client.get(
            reverse("supplier-reorder-response", kwargs={"token": supplier_request.token})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wendani")
        self.assertContains(response, "Sukari")
        self.assertContains(response, 'name="row_ref"', count=2)
        self.assertContains(response, 'class="radio-group can-supply-group"', count=2)
        self.assertContains(response, 'id="can_supply_yes_r0_0"', count=1)
        self.assertContains(response, 'id="can_supply_yes_r1_0"', count=1)

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
            data=self._build_branch_response_payload(
                supplier_request,
                [("Wendani", True, None)],
            ),
        )

        self.assertEqual(response.status_code, 200)
        supplier_request.refresh_from_db()
        reorder.refresh_from_db()
        self.assertEqual(supplier_request.status, SupplierReorderRequest.STATUS_ACCEPTED)
        self.assertEqual(supplier_request.fulfilled_quantity, 8)
        self.assertEqual(reorder.remaining_quantity, 0)
        self.assertEqual(reorder.status, AutoReorderRequest.STATUS_FULFILLED)

    @patch("products.views.notify_next_supplier.delay")
    def test_success_page_shows_confirmed_products_grouped_by_branch(self, _notify_delay):
        now = timezone.now()
        product = Product.objects.create(
            name="Confirmed Branch Item",
            barcode="TEST-CONFIRM-002",
            unit_price=Decimal("12.00"),
            cost_price=Decimal("6.00"),
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
            branch_requirements={"Wendani": 5, "Sukari": 3},
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
            data=self._build_branch_response_payload(
                supplier_request,
                [("Wendani", True, 3), ("Sukari", True, 3)],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Feedback received successfully. Thank you for your response.")
        self.assertContains(response, "Confirmed Supply Summary")
        self.assertContains(response, "Wendani")
        self.assertContains(response, "Confirmed Branch Item: 3 unit(s)")
        self.assertContains(response, "Sukari")
        self.assertContains(response, "Confirmed Branch Item: 3 unit(s)")

    @patch("products.views.notify_next_supplier.delay")
    def test_used_link_still_shows_confirmed_product_in_read_only_mode(self, _notify_delay):
        now = timezone.now()
        product = Product.objects.create(
            name="Confirmed Reference Item",
            barcode="TEST-CONFIRM-READONLY-001",
            unit_price=Decimal("12.00"),
            cost_price=Decimal("6.00"),
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
            branch_requirements={"Wendani": 5, "Sukari": 3},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        supplier_request = SupplierReorderRequest.objects.create(
            reorder_request=reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=8,
            expires_at=now + timedelta(hours=1),
        )

        self.client.post(
            reverse("supplier-reorder-response", kwargs={"token": supplier_request.token}),
            data=self._build_branch_response_payload(
                supplier_request,
                [("Wendani", True, 3), ("Sukari", True, 3)],
            ),
        )
        revisit_response = self.client.get(
            reverse("supplier-reorder-response", kwargs={"token": supplier_request.token})
        )

        self.assertEqual(revisit_response.status_code, 200)
        self.assertContains(revisit_response, "already been used")
        self.assertContains(revisit_response, "Confirmed Reference Item")
        self.assertContains(revisit_response, "Current Status")
        self.assertContains(revisit_response, "Partial")
        self.assertContains(revisit_response, "Confirmed Supply Summary")
        self.assertNotContains(revisit_response, "Confirm Submission")

    @patch("products.views.notify_next_supplier.delay")
    def test_second_branch_yes_is_recorded_when_first_branch_is_no(self, _notify_delay):
        now = timezone.now()
        product = Product.objects.create(
            name="Second Branch Response Item",
            barcode="TEST-CONFIRM-BRANCH-002",
            unit_price=Decimal("12.00"),
            cost_price=Decimal("6.00"),
            reorder_level=10,
            max_stock=100,
            pack_quantity=1,
            is_active=True,
        )
        reorder = AutoReorderRequest.objects.create(
            product=product,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=10,
            remaining_quantity=10,
            branch_requirements={"Wendani": 6, "Sukari": 4},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        supplier_request = SupplierReorderRequest.objects.create(
            reorder_request=reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=10,
            expires_at=now + timedelta(hours=1),
        )

        response = self.client.post(
            reverse("supplier-reorder-response", kwargs={"token": supplier_request.token}),
            data=self._build_branch_response_payload(
                supplier_request,
                [("Wendani", False, None), ("Sukari", True, None)],
            ),
        )

        self.assertEqual(response.status_code, 200)
        supplier_request.refresh_from_db()
        reorder.refresh_from_db()
        self.assertEqual(supplier_request.status, SupplierReorderRequest.STATUS_PARTIAL)
        self.assertEqual(supplier_request.fulfilled_quantity, 4)
        self.assertEqual(reorder.remaining_quantity, 6)


class NotifyNextSupplierSmsDedupTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(
            name="Grouped SMS Supplier",
            contact_person="Batch Contact",
            phone_number="254700000777",
            email="grouped-sms@example.com",
            address="Nairobi",
            priority=1,
        )
        self.product_one = Product.objects.create(
            name="Grouped SMS Product One",
            barcode="NOTIFY-SMS-001",
            unit_price=Decimal("20.00"),
            cost_price=Decimal("10.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        self.product_two = Product.objects.create(
            name="Grouped SMS Product Two",
            barcode="NOTIFY-SMS-002",
            unit_price=Decimal("40.00"),
            cost_price=Decimal("25.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            is_active=True,
        )
        self.reorder_one = AutoReorderRequest.objects.create(
            product=self.product_one,
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={"Wendani": 5},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        self.reorder_two = AutoReorderRequest.objects.create(
            product=self.product_two,
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={"Wendani": 5},
            status=AutoReorderRequest.STATUS_OPEN,
        )

    @patch("products.tasks.expire_supplier_request.apply_async")
    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.send_mail")
    def test_notify_next_supplier_sends_only_one_email_and_sms_per_supplier(self, send_mail_mock, send_sms_mock, _expire_async):
        send_mail_mock.return_value = 1
        send_sms_mock.return_value = {"success": True, "response": {"ok": True}}

        first_result = notify_next_supplier(self.reorder_one.id)
        second_result = notify_next_supplier(self.reorder_two.id)

        self.assertEqual(first_result.get("status"), "email_sent")
        self.assertEqual(first_result.get("sms_status"), "sms_sent")
        self.assertEqual(second_result.get("status"), "notification_suppressed_existing_pending_supplier")
        self.assertEqual(second_result.get("sms_status"), "skipped_existing_pending_supplier_notification")
        send_mail_mock.assert_called_once()
        send_sms_mock.assert_called_once()
        self.assertEqual(
            SupplierReorderRequest.objects.filter(
                supplier=self.supplier,
                status=SupplierReorderRequest.STATUS_PENDING,
            ).count(),
            2,
        )

    @patch("products.tasks.expire_supplier_request.apply_async")
    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.send_mail")
    def test_notify_next_supplier_suppresses_second_auto_notification_same_day(self, send_mail_mock, send_sms_mock, _expire_async):
        send_mail_mock.return_value = 1
        send_sms_mock.return_value = {"success": True, "response": {"ok": True}}

        first_result = notify_next_supplier(self.reorder_one.id)
        SupplierReorderRequest.objects.filter(reorder_request=self.reorder_one).update(
            status=SupplierReorderRequest.STATUS_ACCEPTED,
            fulfilled_quantity=5,
            responded_at=timezone.now(),
        )

        auto_reorder_three = AutoReorderRequest.objects.create(
            product=Product.objects.create(
                name="Grouped SMS Product Three",
                barcode="NOTIFY-SMS-003",
                unit_price=Decimal("60.00"),
                cost_price=Decimal("30.00"),
                reorder_level=5,
                max_stock=50,
                pack_quantity=10,
                is_active=True,
            ),
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={"Kahawa": 5},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        second_result = notify_next_supplier(auto_reorder_three.id)

        self.assertEqual(first_result.get("status"), "email_sent")
        self.assertEqual(second_result.get("status"), "notification_suppressed_daily_limit")
        send_mail_mock.assert_called_once()
        send_sms_mock.assert_called_once()

    @patch("products.tasks.expire_supplier_request.apply_async")
    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.send_mail")
    def test_notify_next_supplier_manual_order_is_exempt_from_daily_auto_limit(self, send_mail_mock, send_sms_mock, _expire_async):
        send_mail_mock.return_value = 1
        send_sms_mock.return_value = {"success": True, "response": {"ok": True}}

        historical_auto_reorder = AutoReorderRequest.objects.create(
            product=self.product_one,
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=0,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={"Wendani": 5},
            status=AutoReorderRequest.STATUS_FULFILLED,
        )
        SupplierReorderRequest.objects.create(
            reorder_request=historical_auto_reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=5,
            fulfilled_quantity=5,
            status=SupplierReorderRequest.STATUS_ACCEPTED,
            expires_at=timezone.now() - timedelta(hours=1),
            emailed_at=timezone.now(),
            responded_at=timezone.now(),
        )

        manual_reorder = AutoReorderRequest.objects.create(
            product=self.product_two,
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            branch_requirements={"Sukari": 5},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        result = notify_next_supplier(manual_reorder.id)

        self.assertEqual(result.get("status"), "email_sent")
        self.assertEqual(result.get("sms_status"), "sms_sent")
        send_mail_mock.assert_called_once()
        send_sms_mock.assert_called_once()

    @patch("products.tasks.expire_supplier_request.apply_async")
    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.send_mail")
    def test_notify_next_supplier_manual_orders_are_batched_per_supplier(self, send_mail_mock, send_sms_mock, _expire_async):
        send_mail_mock.return_value = 1
        send_sms_mock.return_value = {"success": True, "response": {"ok": True}}

        self.reorder_one.origin = AutoReorderRequest.ORIGIN_MANUAL
        self.reorder_one.save(update_fields=["origin"])
        self.reorder_two.origin = AutoReorderRequest.ORIGIN_MANUAL
        self.reorder_two.save(update_fields=["origin"])

        first_result = notify_next_supplier(self.reorder_one.id)
        second_result = notify_next_supplier(self.reorder_two.id)

        self.assertEqual(first_result.get("status"), "email_sent")
        self.assertEqual(first_result.get("sms_status"), "sms_sent")
        self.assertEqual(second_result.get("status"), "notification_suppressed_existing_pending_supplier")
        self.assertEqual(second_result.get("sms_status"), "skipped_existing_pending_supplier_notification")
        send_mail_mock.assert_called_once()
        send_sms_mock.assert_called_once()
        self.assertEqual(
            SupplierReorderRequest.objects.filter(
                supplier=self.supplier,
                status=SupplierReorderRequest.STATUS_PENDING,
            ).exclude(emailed_at__isnull=True).count(),
            1,
        )

    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.send_mail")
    def test_notify_next_supplier_manual_unregistered_creates_expected_receivable_line(self, send_mail_mock, send_sms_mock):
        send_mail_mock.return_value = 1
        send_sms_mock.return_value = {"success": True, "response": {"ok": True}}

        self.reorder_one.origin = AutoReorderRequest.ORIGIN_MANUAL
        self.reorder_one.unregistered_supplier_name = "Walk-in Supplier"
        self.reorder_one.save(update_fields=["origin", "unregistered_supplier_name"])

        result = notify_next_supplier(self.reorder_one.id)

        self.assertEqual(result.get("status"), "manual_unregistered_supplier_ready_for_receiving")
        self.assertEqual(send_mail_mock.call_count, 0)
        self.assertEqual(send_sms_mock.call_count, 0)

        self.reorder_one.refresh_from_db()
        self.assertEqual(self.reorder_one.status, AutoReorderRequest.STATUS_FULFILLED)
        self.assertEqual(self.reorder_one.remaining_quantity, 0)

        req = SupplierReorderRequest.objects.select_related("supplier").get(reorder_request=self.reorder_one)
        self.assertEqual(req.supplier.name, "Unregistered Supplier")
        self.assertEqual(req.status, SupplierReorderRequest.STATUS_ACCEPTED)
        self.assertEqual(req.pending_quantity, req.requested_quantity)

    @patch("products.tasks.send_supplier_reorder_sms.retry")
    @patch("products.tasks.send_sms_via_leopard")
    def test_send_supplier_reorder_sms_returns_failed_when_retries_exhausted(self, send_sms_mock, retry_mock):
        send_sms_mock.return_value = {"success": False, "reason": "request_error"}
        retry_mock.side_effect = MaxRetriesExceededError("retry cap hit")

        supplier_request = SupplierReorderRequest.objects.create(
            reorder_request=self.reorder_one,
            supplier=self.supplier,
            priority=1,
            requested_quantity=5,
            status=SupplierReorderRequest.STATUS_PENDING,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        result = send_supplier_reorder_sms(supplier_request.id)

        self.assertEqual(result.get("status"), "sms_failed")
        self.assertEqual(result.get("supplier_request_id"), supplier_request.id)

class ExhaustionAlertTaskTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(
            name="Alert Supplier",
            contact_person="Alert User",
            phone_number="254700000123",
            email="alert-supplier@example.com",
            address="Nairobi",
            priority=1,
        )
        self.product = Product.objects.create(
            name="Alert Product",
            barcode="ALERT-PROD-001",
            unit_price=Decimal("20.00"),
            cost_price=Decimal("10.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=5,
            is_active=True,
        )
        self.reorder = AutoReorderRequest.objects.create(
            product=self.product,
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=10,
            remaining_quantity=10,
            branch_requirements={"Wendani": 6, "Sukari": 4},
            status=AutoReorderRequest.STATUS_EXHAUSTED,
            admin_notified=False,
        )
        SupplierReorderRequest.objects.create(
            reorder_request=self.reorder,
            supplier=self.supplier,
            priority=1,
            requested_quantity=10,
            fulfilled_quantity=0,
            status=SupplierReorderRequest.STATUS_EXPIRED,
            expires_at=timezone.now() - timedelta(hours=2),
            responded_at=timezone.now() - timedelta(hours=1),
        )

    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.send_mail")
    def test_alert_email_success_marks_orders_notified(self, send_mail_mock, send_sms_mock):
        send_mail_mock.return_value = 1
        result = send_batched_exhaustion_alerts()

        self.assertEqual(result["status"], "alerts_sent")
        self.reorder.refresh_from_db()
        self.assertTrue(self.reorder.admin_notified)
        send_mail_mock.assert_called_once()
        send_sms_mock.assert_called_once()

    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.send_mail")
    def test_alert_email_failure_does_not_mark_orders_notified(self, send_mail_mock, send_sms_mock):
        send_mail_mock.side_effect = RuntimeError("smtp unavailable")
        result = send_batched_exhaustion_alerts()

        self.assertEqual(result["status"], "email_failed")
        self.reorder.refresh_from_db()
        self.assertFalse(self.reorder.admin_notified)
        send_sms_mock.assert_not_called()


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

    def _mark_manual_product_as_sold(self, product, branch, days_ago=1):
        sale = Sale.objects.create(
            receipt_number=f"MANUAL-SALE-{product.id}-{branch.id}-{days_ago}",
            branch=branch,
            cashier=self.admin_user,
            payment_method="cash",
            cash_amount=Decimal("0.00"),
            mpesa_amount=Decimal("0.00"),
            credit_amount=Decimal("0.00"),
            total_amount=Decimal(product.unit_price),
        )
        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=1,
            unit_price=product.unit_price,
            total_price=Decimal(product.unit_price),
        )
        Sale.objects.filter(pk=sale.pk).update(created_at=timezone.now() - timedelta(days=days_ago))

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
        self.assertEqual(response.headers.get("X-Skip-HX-Refresh"), "true")
        existing_manual.refresh_from_db()
        self.assertEqual(existing_manual.requested_quantity, 4)
        self.assertEqual(existing_manual.remaining_quantity, 4)
        self.assertEqual(existing_manual.branch_requirements, {"Wendani": 4})

        branch_b_reorder_p1 = AutoReorderRequest.objects.get(
            product=self.product_one,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            branch_requirements={"Sukari": 2},
        )
        self.assertEqual(branch_b_reorder_p1.requested_quantity, 2)

        second_reorder = AutoReorderRequest.objects.get(
            product=self.product_two,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
        )
        self.assertEqual(second_reorder.requested_quantity, 4)
        self.assertEqual(second_reorder.branch_requirements, {"Sukari": 4})

        notified_ids = {call.args[0] for call in notify_delay.call_args_list}
        self.assertEqual(notified_ids, {existing_manual.id, branch_b_reorder_p1.id, second_reorder.id})

    @patch("products.views.notify_next_supplier.delay")
    def test_pharmtec_can_create_manual_order_for_assigned_branch(self, notify_delay):
        pharmtec_user = User.objects.create_user(
            username="manual_pharmtec",
            password="pass12345",
            role="pharmtec",
            branch=self.branch_b,
        )
        self.client.force_login(pharmtec_user)

        response = self.client.post(
            reverse("create-order"),
            data={
                "product_id[]": [str(self.product_one.id)],
                "packets[]": ["2"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Skip-HX-Refresh"), "true")
        reorder = AutoReorderRequest.objects.get(
            product=self.product_one,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
        )
        self.assertEqual(reorder.requested_quantity, 2)
        self.assertEqual(reorder.branch_requirements, {self.branch_b.name: 2})
        self.assertEqual(reorder.created_by, pharmtec_user)
        self.assertEqual(reorder.approval_status, AutoReorderRequest.APPROVAL_PENDING)
        notify_delay.assert_not_called()

    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_allows_packets_above_product_max(self, notify_delay):
        response = self.client.post(
            reverse("create-order"),
            data={
                "product_id[]": [str(self.product_two.id)],
                "branch_id[]": [str(self.branch_a.id)],
                "packets[]": ["7"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        manual_reorder = AutoReorderRequest.objects.get(
            product=self.product_two,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
        )
        self.assertEqual(manual_reorder.requested_quantity, 7)
        self.assertEqual(manual_reorder.branch_requirements, {self.branch_a.name: 7})
        notify_delay.assert_called_once_with(manual_reorder.id)

    @patch("products.views._send_branch_supply_request_sms")
    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_routes_dead_stock_product_to_source_branch_and_notifies_branch(self, notify_delay, branch_sms_mock):
        branch_sms_mock.return_value = {"status": "sms_sent"}
        Stock.objects.create(product=self.product_one, branch=self.branch_a, quantity=0)
        Stock.objects.create(product=self.product_one, branch=self.branch_b, quantity=80)
        self._mark_manual_product_as_sold(self.product_one, self.branch_b, days_ago=76)

        response = self.client.post(
            reverse("create-order"),
            data={
                "product_id[]": [str(self.product_one.id)],
                "branch_id[]": [str(self.branch_a.id)],
                "packets[]": ["4"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        branch_request = BranchSupplyRequest.objects.get(product=self.product_one)
        self.assertEqual(branch_request.source_branch, self.branch_b)
        self.assertEqual(branch_request.destination_branch, self.branch_a)
        self.assertEqual(branch_request.requested_quantity, 4)
        self.assertFalse(AutoReorderRequest.objects.filter(product=self.product_one).exists())
        branch_sms_mock.assert_called_once_with(branch_request)
        notify_delay.assert_not_called()

    def test_branch_supply_request_shows_as_expected_stock_from_source_branch(self):
        branch_request = BranchSupplyRequest.objects.create(
            product=self.product_one,
            source_branch=self.branch_b,
            destination_branch=self.branch_a,
            requested_quantity=4,
            status=BranchSupplyRequest.STATUS_PENDING,
        )

        receive_response = self.client.get(reverse("receive-stock"), HTTP_HX_REQUEST="true")
        self.assertEqual(receive_response.status_code, 200)
        self.assertContains(receive_response, f'value="branch-{self.branch_b.id}"')
        self.assertContains(receive_response, "Branch: Sukari")

        rows_response = self.client.get(
            reverse("supplier-pending-orders"),
            data={"supplier_id": f"branch-{self.branch_b.id}", "branch_id": str(self.branch_a.id)},
        )
        self.assertEqual(rows_response.status_code, 200)
        self.assertContains(rows_response, self.product_one.name)
        self.assertContains(rows_response, str(branch_request.requested_quantity))

    def test_receive_stock_from_branch_source_completes_branch_transfer(self):
        Stock.objects.create(product=self.product_one, branch=self.branch_a, quantity=0)
        Stock.objects.create(product=self.product_one, branch=self.branch_b, quantity=80)
        branch_request = BranchSupplyRequest.objects.create(
            product=self.product_one,
            source_branch=self.branch_b,
            destination_branch=self.branch_a,
            requested_quantity=4,
            status=BranchSupplyRequest.STATUS_PENDING,
        )

        response = self.client.post(
            reverse("receive-stock"),
            data={
                "supplier_id": f"branch-{self.branch_b.id}",
                "branch_id": str(self.branch_a.id),
                "branch_supply_request_id[]": [str(branch_request.id)],
                "quantity[]": ["4"],
                "cost_price[]": [str(self.product_one.cost_price)],
                "selling_price[]": [str(self.product_one.unit_price)],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        branch_request.refresh_from_db()
        self.assertEqual(branch_request.status, BranchSupplyRequest.STATUS_COMPLETED)
        self.assertEqual(branch_request.fulfilled_quantity, 4)
        self.assertEqual(Stock.objects.get(product=self.product_one, branch=self.branch_a).quantity, 4)
        self.assertEqual(Stock.objects.get(product=self.product_one, branch=self.branch_b).quantity, 76)

    def test_create_order_markup_indicates_dead_stock_branch_source(self):
        Stock.objects.create(product=self.product_one, branch=self.branch_b, quantity=80)
        self._mark_manual_product_as_sold(self.product_one, self.branch_b, days_ago=76)

        response = self.client.get(
            reverse("create-order"),
            data={"branch": str(self.branch_a.id)},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '"internal_supply"')
        self.assertContains(response, '"source_branch_name": "Sukari"')

    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_capacity_ignores_open_auto_reorders(self, notify_delay):
        AutoReorderRequest.objects.create(
            product=self.product_limited,
            target_stock_level=5,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={self.branch_a.name: 5},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        response = self.client.post(
            reverse("create-order"),
            data={
                "product_id[]": [str(self.product_limited.id)],
                "branch_id[]": [str(self.branch_a.id)],
                "packets[]": ["5"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        manual_reorder = AutoReorderRequest.objects.get(
            product=self.product_limited,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
        )
        self.assertEqual(manual_reorder.requested_quantity, 5)
        self.assertEqual(manual_reorder.branch_requirements, {self.branch_a.name: 5})
        notify_delay.assert_called_once_with(manual_reorder.id)

    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_allows_projected_stock_to_exceed_max(self, notify_delay):
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

        self.assertEqual(response.status_code, 200)
        manual_reorder = AutoReorderRequest.objects.get(
            product=self.product_limited,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
        )
        self.assertEqual(manual_reorder.requested_quantity, 4)
        self.assertEqual(manual_reorder.branch_requirements, {self.branch_a.name: 4})
        notify_delay.assert_called_once_with(manual_reorder.id)

    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_unregistered_supplier_is_created_immediately(self, notify_delay):
        response = self.client.post(
            reverse("create-order"),
            data={
                "supplier_strategy": "unregistered",
                "unregistered_supplier_name": "Street Vendor Ltd",
                "product_id[]": [str(self.product_one.id)],
                "branch_id[]": [str(self.branch_a.id)],
                "packets[]": ["2"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Skip-HX-Refresh"), "true")
        self.assertTrue(Supplier.objects.filter(name__iexact="Unregistered Supplier").exists())
        notify_delay.assert_not_called()

    def test_create_order_marks_exempt_products_in_row_markup(self):
        self.product_one.exempt_from_auto_reorder = True
        self.product_one.save(update_fields=["exempt_from_auto_reorder", "updated_at"])

        response = self.client.get(reverse("create-order"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-exempt="1"')
        self.assertContains(response, self.product_one.name)

    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_unregistered_supplier_items_show_in_pending_orders(self, notify_delay):
        response = self.client.post(
            reverse("create-order"),
            data={
                "supplier_strategy": "unregistered",
                "unregistered_supplier_name": "Any Vendor Name",
                "product_id[]": [str(self.product_one.id)],
                "branch_id[]": [str(self.branch_a.id)],
                "packets[]": ["2"],
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        notify_delay.assert_not_called()

        supplier = Supplier.objects.get(name__iexact="Unregistered Supplier")
        pending_response = self.client.get(
            reverse("supplier-pending-orders"),
            data={"supplier_id": str(supplier.id), "branch_id": str(self.branch_a.id)},
        )
        self.assertEqual(pending_response.status_code, 200)
        self.assertContains(pending_response, self.product_one.name)

    @patch("products.views.notify_next_supplier.delay")
    def test_create_order_updates_exempt_from_auto_reorder_status(self, notify_delay):
        self.product_one.exempt_from_auto_reorder = False
        self.product_one.save()
        self.product_two.exempt_from_auto_reorder = True
        self.product_two.save()

        response = self.client.post(
            reverse("create-order"),
            data={
                "product_id[]": [str(self.product_one.id), str(self.product_two.id)],
                "branch_id[]": [str(self.branch_a.id), str(self.branch_b.id)],
                "packets[]": ["3", "2"],
                "exempt_from_auto_reorder[]": ["1", "0"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        stock_one = Stock.objects.get(product=self.product_one, branch=self.branch_a)
        stock_two = Stock.objects.get(product=self.product_two, branch=self.branch_b)
        self.assertTrue(stock_one.exempt_from_auto_reorder)
        self.assertFalse(stock_two.exempt_from_auto_reorder)

    @patch("products.views.notify_next_supplier.delay")
    def test_bulk_approval_updates_quantities_and_rejects_zero_quantity_orders(self, notify_delay):
        approve_order = AutoReorderRequest.objects.create(
            product=self.product_one,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            created_by=self.admin_user,
            approval_status=AutoReorderRequest.APPROVAL_PENDING,
            branch_requirements={self.branch_a.name: 5},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        reject_order = AutoReorderRequest.objects.create(
            product=self.product_two,
            target_stock_level=60,
            current_stock_snapshot=0,
            requested_quantity=3,
            remaining_quantity=3,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            created_by=self.admin_user,
            approval_status=AutoReorderRequest.APPROVAL_PENDING,
            branch_requirements={self.branch_b.name: 3},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        response = self.client.post(
            reverse("manual-order-bulk-approval"),
            data={
                "order_ids[]": [str(approve_order.id), str(reject_order.id)],
                f"quantity_{approve_order.id}": "4",
                f"quantity_{reject_order.id}": "0",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Orders have been approved")

        approve_order.refresh_from_db()
        self.assertEqual(approve_order.approval_status, AutoReorderRequest.APPROVAL_APPROVED)
        self.assertEqual(approve_order.requested_quantity, 4)
        self.assertEqual(approve_order.remaining_quantity, 4)
        self.assertEqual(approve_order.branch_requirements, {self.branch_a.name: 4})
        self.assertEqual(approve_order.approved_by, self.admin_user)

        reject_order.refresh_from_db()
        self.assertEqual(reject_order.approval_status, AutoReorderRequest.APPROVAL_REJECTED)
        self.assertEqual(reject_order.status, AutoReorderRequest.STATUS_CANCELLED)
        self.assertEqual(reject_order.remaining_quantity, 0)
        self.assertEqual(reject_order.approved_by, self.admin_user)

        notify_delay.assert_called_once_with(approve_order.id)
        self.assertFalse(
            AutoReorderRequest.objects.filter(
                origin=AutoReorderRequest.ORIGIN_MANUAL,
                approval_status=AutoReorderRequest.APPROVAL_PENDING,
            ).exists()
        )

    @patch("products.views.notify_next_supplier.delay")
    def test_manual_order_approval_list_view_filters_by_branch(self, notify_delay):
        order_branch_a = AutoReorderRequest.objects.create(
            product=self.product_one,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            created_by=self.admin_user,
            approval_status=AutoReorderRequest.APPROVAL_PENDING,
            branch_requirements={self.branch_a.name: 5},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        order_branch_b = AutoReorderRequest.objects.create(
            product=self.product_one,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=3,
            remaining_quantity=3,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            created_by=self.admin_user,
            approval_status=AutoReorderRequest.APPROVAL_PENDING,
            branch_requirements={self.branch_b.name: 3},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        response_all = self.client.get(reverse("order-approvals"))
        self.assertEqual(response_all.status_code, 200)
        self.assertContains(response_all, self.branch_a.name)
        self.assertContains(response_all, self.branch_b.name)
        self.assertEqual(len(response_all.context["pending_manual_orders"]), 2)

        response_branch_a = self.client.get(reverse("order-approvals"), data={"branch": str(self.branch_a.id)})
        self.assertEqual(response_branch_a.status_code, 200)
        self.assertEqual(len(response_branch_a.context["pending_manual_orders"]), 1)
        self.assertEqual(response_branch_a.context["pending_manual_orders"][0].id, order_branch_a.id)

    @patch("products.views.notify_next_supplier.delay")
    def test_manual_order_approval_per_branch_allows_independent_approvals(self, notify_delay):
        order_branch_a = AutoReorderRequest.objects.create(
            product=self.product_one,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=5,
            remaining_quantity=5,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            created_by=self.admin_user,
            approval_status=AutoReorderRequest.APPROVAL_PENDING,
            branch_requirements={self.branch_a.name: 5},
            status=AutoReorderRequest.STATUS_OPEN,
        )
        order_branch_b = AutoReorderRequest.objects.create(
            product=self.product_one,
            target_stock_level=100,
            current_stock_snapshot=0,
            requested_quantity=3,
            remaining_quantity=3,
            origin=AutoReorderRequest.ORIGIN_MANUAL,
            created_by=self.admin_user,
            approval_status=AutoReorderRequest.APPROVAL_PENDING,
            branch_requirements={self.branch_b.name: 3},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        response = self.client.post(
            reverse("manual-order-bulk-approval"),
            data={
                "branch_id": str(self.branch_a.id),
                "order_ids[]": [str(order_branch_a.id)],
                f"quantity_{order_branch_a.id}": "4",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"branch={self.branch_a.id}", response.redirect_chain[0][0])
        order_branch_a.refresh_from_db()
        order_branch_b.refresh_from_db()

        self.assertEqual(order_branch_a.approval_status, AutoReorderRequest.APPROVAL_APPROVED)
        self.assertEqual(order_branch_a.requested_quantity, 4)
        self.assertEqual(order_branch_b.approval_status, AutoReorderRequest.APPROVAL_PENDING)
        self.assertEqual(order_branch_b.requested_quantity, 3)


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

    @patch("products.views.notify_next_supplier.delay")
    def test_receive_stock_accepts_all_zero_quantities(self, notify_delay):
        response = self.client.post(
            reverse("receive-stock"),
            data={
                "supplier_id": str(self.supplier.id),
                "invoice_number": "INV-ZERO-RECEIVE",
                "reorder_request_id[]": [str(self.supplier_request.id)],
                "quantity[]": ["0"],
                "cost_price[]": ["90.00"],
                "selling_price[]": ["120.00"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("stock-action-success", response.headers.get("HX-Trigger", ""))
        self.assertEqual(Purchase.objects.count(), 0)
        self.supplier_request.refresh_from_db()
        self.reorder.refresh_from_db()
        self.assertEqual(self.supplier_request.status, SupplierReorderRequest.STATUS_REJECTED)
        self.assertEqual(self.supplier_request.fulfilled_quantity, 0)
        self.assertEqual(self.supplier_request.received_quantity, 0)
        self.assertEqual(self.reorder.status, AutoReorderRequest.STATUS_OPEN)
        self.assertEqual(self.reorder.remaining_quantity, self.reorder.requested_quantity)
        notify_delay.assert_called_once_with(self.reorder.id)

        pending_response = self.client.get(
            reverse("supplier-pending-orders"),
            data={"supplier_id": str(self.supplier.id), "branch_id": str(self.branch.id)},
        )
        self.assertEqual(pending_response.status_code, 200)
        self.assertContains(pending_response, "No accepted pending orders found for this supplier.")
        self.assertNotContains(pending_response, self.product.name)

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
    def test_receive_stock_does_not_queue_supplier_pdf_confirmation_when_disabled(self, queue_pdf_task):
        response = self.client.post(
            reverse("receive-stock"),
            data={
                "supplier_id": str(self.supplier.id),
                "invoice_number": "INV-CONFIRM-PDF-DISABLED",
                "reorder_request_id[]": [str(self.supplier_request.id)],
                "quantity[]": ["2"],
                "cost_price[]": ["90.00"],
                "selling_price[]": ["120.00"],
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Purchase.objects.count(), 1)
        queue_pdf_task.assert_not_called()

    @override_settings(SUPPLIER_RECEIPT_EMAIL_ENABLED=True)
    @patch("products.views.send_purchase_confirmation_to_supplier.delay")
    def test_receive_stock_queues_supplier_pdf_confirmation_when_enabled(self, queue_pdf_task):
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

    @override_settings(SUPPLIER_RECEIPT_EMAIL_ENABLED=True)
    @patch("products.views.send_purchase_confirmation_to_supplier.delay")
    def test_receive_stock_rejects_duplicate_invoice_submission(self, queue_pdf_task):
        payload = {
            "supplier_id": str(self.supplier.id),
            "invoice_number": "INV-DUPLICATE-001",
            "reorder_request_id[]": [str(self.supplier_request.id)],
            "quantity[]": ["2"],
            "cost_price[]": ["90.00"],
            "selling_price[]": ["120.00"],
        }

        first_response = self.client.post(
            reverse("receive-stock"),
            data=payload,
            HTTP_HX_REQUEST="true",
        )
        second_response = self.client.post(
            reverse("receive-stock"),
            data=payload,
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertContains(second_response, "already been received")
        self.assertEqual(Purchase.objects.count(), 1)
        queue_pdf_task.assert_called_once()


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

    @patch("products.tasks.send_purchase_confirmation_sms.delay")
    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.EmailMessage")
    def test_send_purchase_confirmation_is_idempotent_for_duplicate_task_runs(
        self, email_cls, sms_send, queue_sms_task
    ):
        sms_send.return_value = {"success": True, "response": {"ok": True}}
        first = send_purchase_confirmation_to_supplier(self.purchase.id)
        second = send_purchase_confirmation_to_supplier(self.purchase.id)

        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "already_sent")
        email_cls.return_value.send.assert_called_once_with(fail_silently=False)
        sms_send.assert_called_once()
        queue_sms_task.assert_not_called()
        self.purchase.refresh_from_db()
        self.assertIsNotNone(self.purchase.supplier_confirmation_emailed_at)

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

    @patch("products.tasks.send_purchase_confirmation_sms.retry")
    @patch("products.tasks.send_sms_via_leopard")
    def test_send_purchase_confirmation_sms_returns_failed_when_retries_exhausted(self, sms_send, retry_mock):
        sms_send.return_value = {"success": False, "reason": "request_error"}
        retry_mock.side_effect = MaxRetriesExceededError("retry cap hit")

        result = send_purchase_confirmation_sms(self.purchase.id)

        self.assertEqual(result["status"], "sms_failed")
        self.assertEqual(result["purchase_id"], self.purchase.id)

    @patch("products.tasks.send_sms_via_leopard")
    def test_send_purchase_confirmation_sms_skips_when_not_configured(self, sms_send):
        sms_send.return_value = {"success": False, "reason": "not_configured"}
        result = send_purchase_confirmation_sms(self.purchase.id)

        self.assertEqual(result["status"], "sms_skipped")
        self.assertEqual(result["reason"], "not_configured")


class AdjustStockBranchIndependenceTests(TestCase):
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
        self.admin = User.objects.create_user(
            username="admin_user",
            password="pass12345",
            role="super_admin",
            branch=self.wendani,
        )
        self.product = Product.objects.create(
            name="Branch Independence Test Product",
            barcode="TEST-IND-001",
            unit_price=Decimal("15.00"),
            cost_price=Decimal("8.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=10,
            exempt_from_auto_reorder=False,
            is_active=True,
        )
        self.client.force_login(self.admin)

    def test_adjust_stock_exempts_only_selected_branch(self):
        # Adjust stock for Wendani with exempt_from_auto_reorder = 1 via HTMX
        response = self.client.post(
            reverse("adjust-stock", kwargs={"pk": self.product.pk}),
            data={
                "branch_id": str(self.wendani.id),
                "new_quantity": "25",
                "exempt_from_auto_reorder": "1",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)

        wendani_stock = Stock.objects.get(product=self.product, branch=self.wendani)
        sukari_stock, _ = Stock.objects.get_or_create(product=self.product, branch=self.sukari, defaults={"quantity": 0})
        self.product.refresh_from_db()

        # Wendani stock is exempt; Sukari stock and global Product are NOT exempt
        self.assertTrue(wendani_stock.exempt_from_auto_reorder)
        self.assertFalse(sukari_stock.exempt_from_auto_reorder)
        self.assertFalse(self.product.exempt_from_auto_reorder)

    def test_adjust_stock_unexempts_only_selected_branch(self):
        # Both Wendani and Sukari stocks are initially exempt
        Stock.objects.create(product=self.product, branch=self.wendani, quantity=10, exempt_from_auto_reorder=True)
        sukari_stock = Stock.objects.create(product=self.product, branch=self.sukari, quantity=20, exempt_from_auto_reorder=True)

        # Unexempt Wendani stock via POST
        response = self.client.post(
            reverse("adjust-stock", kwargs={"pk": self.product.pk}),
            data={
                "branch_id": str(self.wendani.id),
                "new_quantity": "15",
                "exempt_from_auto_reorder": "0",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)

        wendani_stock = Stock.objects.get(product=self.product, branch=self.wendani)
        sukari_stock.refresh_from_db()

        # Wendani is unexempted; Sukari remains exempted
        self.assertFalse(wendani_stock.exempt_from_auto_reorder)
        self.assertTrue(sukari_stock.exempt_from_auto_reorder)

    def test_product_edit_exempts_only_selected_branch(self):
        # Edit product while active branch is Wendani with exempt_from_auto_reorder = 1
        response = self.client.post(
            reverse("product-edit", kwargs={"pk": self.product.pk}),
            data={
                "branch_id": str(self.wendani.id),
                "name": self.product.name,
                "unit_price": str(self.product.unit_price),
                "cost_price": str(self.product.cost_price),
                "reorder_level": str(self.product.reorder_level),
                "max_stock": str(self.product.max_stock),
                "pack_quantity": str(self.product.pack_quantity),
                "exempt_from_auto_reorder": "1",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)

        wendani_stock = Stock.objects.get(product=self.product, branch=self.wendani)
        sukari_stock, _ = Stock.objects.get_or_create(product=self.product, branch=self.sukari, defaults={"quantity": 0})
        self.product.refresh_from_db()

        # Wendani stock is exempt; Sukari stock and global product model are NOT exempt
        self.assertTrue(wendani_stock.exempt_from_auto_reorder)
        self.assertFalse(sukari_stock.exempt_from_auto_reorder)
        self.assertFalse(self.product.exempt_from_auto_reorder)


class ZeroMovementExemptCommandTests(TestCase):
    def setUp(self):
        self.wendani = Branch.objects.create(
            name="Wendani",
            code="WEN-EX",
            address="Wendani",
            phone_number="0700000001",
            is_active=True,
        )
        self.sukari = Branch.objects.create(
            name="Sukari",
            code="SUK-EX",
            address="Sukari",
            phone_number="0700000002",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="ex_admin",
            password="pass12345",
            role="super_admin",
            branch=self.wendani,
        )
        # Product 1: Zero movements in both branches
        self.product_zero_mov = Product.objects.create(
            name="Zero Movement Prod",
            barcode="EX-ZERO-001",
            unit_price=Decimal("100.00"),
            cost_price=Decimal("70.00"),
            is_active=True,
        )
        # Product 2: Movement in Wendani, zero in Sukari
        self.product_mixed_mov = Product.objects.create(
            name="Mixed Movement Prod",
            barcode="EX-MIX-001",
            unit_price=Decimal("50.00"),
            cost_price=Decimal("30.00"),
            is_active=True,
        )
        Stock.objects.create(product=self.product_mixed_mov, branch=self.wendani, quantity=10)

        StockMovement.objects.create(
            product=self.product_mixed_mov,
            branch=self.wendani,
            movement_type="purchase",
            quantity=10,
            created_by=self.user,
        )

    def test_exempt_zero_movement_command_exempts_zero_movement_stocks(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("exempt_zero_movement_stock", stdout=out)
        output = out.getvalue()

        self.assertIn("SUMMARY", output)
        zero_wendani = Stock.objects.get(product=self.product_zero_mov, branch=self.wendani)
        zero_sukari = Stock.objects.get(product=self.product_zero_mov, branch=self.sukari)
        mixed_wendani = Stock.objects.get(product=self.product_mixed_mov, branch=self.wendani)
        mixed_sukari = Stock.objects.get(product=self.product_mixed_mov, branch=self.sukari)

        # Zero movement product exempted in both branches
        self.assertTrue(zero_wendani.exempt_from_auto_reorder)
        self.assertTrue(zero_sukari.exempt_from_auto_reorder)

        # Mixed movement product exempted in Sukari (0 movements), NOT in Wendani (has movements)
        self.assertFalse(mixed_wendani.exempt_from_auto_reorder)
        self.assertTrue(mixed_sukari.exempt_from_auto_reorder)

    def test_exempt_zero_movement_command_dry_run(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("exempt_zero_movement_stock", dry_run=True, stdout=out)
        output = out.getvalue()

        self.assertIn("DRY-RUN", output)
        # In dry run mode, stock rows should not be saved or created in DB
        self.assertFalse(Stock.objects.filter(product=self.product_zero_mov).exists())

    def test_exempt_zero_movement_command_branch_filter(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("exempt_zero_movement_stock", branch="Wendani", stdout=out)

        # Only Wendani stock created/updated
        self.assertTrue(Stock.objects.filter(product=self.product_zero_mov, branch=self.wendani).exists())
        self.assertFalse(Stock.objects.filter(product=self.product_zero_mov, branch=self.sukari).exists())


class PerBranchNotificationTests(TestCase):
    def setUp(self):
        self.wendani = Branch.objects.create(
            name="Wendani",
            code="WEN-PB",
            address="Wendani",
            phone_number="0700000010",
            is_active=True,
        )
        self.sukari = Branch.objects.create(
            name="Sukari",
            code="SUK-PB",
            address="Sukari",
            phone_number="0700000020",
            is_active=True,
        )
        self.supplier = Supplier.objects.create(
            name="Apex Pharma",
            email="apex@example.com",
            phone_number="0711111111",
            contact_person="Dr. Apex",
            priority=1,
        )
        self.product = Product.objects.create(
            name="Panadol Extra 500mg",
            barcode="PAN-PB-001",
            unit_price=Decimal("10.00"),
            cost_price=Decimal("6.00"),
            is_active=True,
        )

    @patch("products.tasks.send_sms_via_leopard")
    @patch("products.tasks.send_mail")
    def test_per_branch_notifications_sent_with_branch_name(self, send_mail_mock, send_sms_mock):
        send_mail_mock.return_value = 1
        send_sms_mock.return_value = {"success": True, "response": {"ok": True}}

        reorder = AutoReorderRequest.objects.create(
            product=self.product,
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=7,
            remaining_quantity=7,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={"Wendani": 4, "Sukari": 3},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        result = notify_next_supplier(reorder.id)

        self.assertEqual(result.get("status"), "email_sent")
        self.assertEqual(result.get("notifications_sent"), 2)

        self.assertEqual(send_mail_mock.call_count, 2)
        subjects = [call.kwargs.get("subject") for call in send_mail_mock.call_args_list]
        bodies = [call.kwargs.get("message") for call in send_mail_mock.call_args_list]

        self.assertIn("Purchase Order - Wendani", subjects)
        self.assertIn("Purchase Order - Sukari", subjects)
        self.assertTrue(any("Branch: Wendani" in body for body in bodies))
        self.assertTrue(any("Branch: Sukari" in body for body in bodies))

    def test_supplier_response_link_filters_by_branch(self):
        reorder = AutoReorderRequest.objects.create(
            product=self.product,
            target_stock_level=50,
            current_stock_snapshot=0,
            requested_quantity=7,
            remaining_quantity=7,
            origin=AutoReorderRequest.ORIGIN_AUTO,
            branch_requirements={"Wendani": 4, "Sukari": 3},
            status=AutoReorderRequest.STATUS_OPEN,
        )

        req_wendani = SupplierReorderRequest.objects.create(
            reorder_request=reorder,
            supplier=self.supplier,
            branch=self.wendani,
            priority=1,
            requested_quantity=4,
            expires_at=timezone.now() + timedelta(hours=3),
        )
        req_sukari = SupplierReorderRequest.objects.create(
            reorder_request=reorder,
            supplier=self.supplier,
            branch=self.sukari,
            priority=1,
            requested_quantity=3,
            expires_at=timezone.now() + timedelta(hours=3),
        )

        resp_wendani = self.client.get(reverse("supplier-reorder-response", kwargs={"token": req_wendani.token}))
        self.assertEqual(resp_wendani.status_code, 200)
        branch_groups_wendani = resp_wendani.context["branch_groups"]
        self.assertEqual(len(branch_groups_wendani), 1)
        self.assertEqual(branch_groups_wendani[0]["name"], "Wendani")

        resp_sukari = self.client.get(reverse("supplier-reorder-response", kwargs={"token": req_sukari.token}))
        self.assertEqual(resp_sukari.status_code, 200)
        branch_groups_sukari = resp_sukari.context["branch_groups"]
        self.assertEqual(len(branch_groups_sukari), 1)
        self.assertEqual(branch_groups_sukari[0]["name"], "Sukari")


class ProductEditBranchStockTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name="Wendani",
            code="WEN-ED",
            address="Wendani",
            phone_number="0700000030",
            is_active=True,
        )
        self.user = User.objects.create_superuser(
            username="admin_edit_test",
            email="admin_edit@example.com",
            password="password123",
            branch=self.branch,
        )
        self.product = Product.objects.create(
            name="Amoxicillin 500mg",
            barcode="AMX-001",
            unit_price=Decimal("15.00"),
            cost_price=Decimal("10.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=1,
            is_active=True,
        )
        self.stock = Stock.objects.create(
            product=self.product,
            branch=self.branch,
            quantity=10,
        )
        self.client.force_login(self.user)

    def test_product_edit_updates_branch_stock_quantity(self):
        response = self.client.post(
            reverse("product-edit", kwargs={"pk": self.product.pk}),
            {
                "name": "Amoxicillin 500mg Updated",
                "barcode": "AMX-001",
                "unit_price": "18.00",
                "cost_price": "12.00",
                "reorder_level": "5",
                "max_stock": "50",
                "pack_quantity": "1",
                "stock_quantity": "35",
                "branch_id": str(self.branch.id),
            },
        )
        self.assertEqual(response.status_code, 200)

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 35)

        movement = StockMovement.objects.filter(product=self.product, branch=self.branch).latest("id")
        self.assertEqual(movement.movement_type, "adjustment")
        self.assertEqual(movement.quantity, 25)


