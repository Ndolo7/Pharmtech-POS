import sys
from django.core.management.base import BaseCommand
from django.db import transaction
from branches.models import Branch
from products.models import Product, Stock, StockMovement, PurchaseItem, TransferItem
from sales.models import SaleItem


class Command(BaseCommand):
    help = (
        "Exempt all stock with zero product movements in the product trail "
        "(in respective branches) from auto-reorder."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate execution without committing changes to the database.",
        )
        parser.add_argument(
            "--branch",
            type=str,
            help="Filter by branch name or branch ID.",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Include inactive products and inactive branches.",
        )
        parser.add_argument(
            "--skip-create-missing",
            action="store_true",
            help="Do not create missing Stock rows for zero-movement product-branch pairs.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        branch_filter = options.get("branch")
        include_inactive = options["include_inactive"]
        skip_create_missing = options["skip_create_missing"]

        if dry_run:
            self.stdout.write(self.style.WARNING("=== RUNNING IN DRY-RUN MODE (NO DB CHANGES WILL BE SAVED) ==="))

        # Filter branches
        branch_qs = Branch.objects.all()
        if not include_inactive:
            branch_qs = branch_qs.filter(is_active=True)

        if branch_filter:
            if branch_filter.isdigit():
                branch_qs = branch_qs.filter(pk=int(branch_filter))
            else:
                branch_qs = branch_qs.filter(name__icontains=branch_filter)

        branches = list(branch_qs.order_by("name"))
        if not branches:
            self.stdout.write(self.style.ERROR(f"No branches found matching query: {branch_filter}"))
            return

        branch_ids = [b.id for b in branches]

        # Filter products
        product_qs = Product.objects.all()
        if not include_inactive:
            product_qs = product_qs.filter(is_active=True)

        products = list(product_qs.order_by("id"))
        if not products:
            self.stdout.write(self.style.ERROR("No products found."))
            return

        self.stdout.write(
            f"Evaluating {len(products)} products across {len(branches)} branch(es)..."
        )

        # 1. Gather all product-branch pairs that HAVE movement records
        moved_pairs = set()

        sm_qs = StockMovement.objects.filter(branch_id__in=branch_ids).values_list("product_id", "branch_id").distinct()
        moved_pairs.update((p_id, b_id) for p_id, b_id in sm_qs if p_id and b_id)

        si_qs = SaleItem.objects.filter(sale__branch_id__in=branch_ids).values_list("product_id", "sale__branch_id").distinct()
        moved_pairs.update((p_id, b_id) for p_id, b_id in si_qs if p_id and b_id)

        pi_qs = PurchaseItem.objects.filter(purchase__branch_id__in=branch_ids).values_list("product_id", "purchase__branch_id").distinct()
        moved_pairs.update((p_id, b_id) for p_id, b_id in pi_qs if p_id and b_id)

        ti_in_qs = TransferItem.objects.filter(transfer__to_branch_id__in=branch_ids).values_list("product_id", "transfer__to_branch_id").distinct()
        moved_pairs.update((p_id, b_id) for p_id, b_id in ti_in_qs if p_id and b_id)

        ti_out_qs = TransferItem.objects.filter(transfer__from_branch_id__in=branch_ids).values_list("product_id", "transfer__from_branch_id").distinct()
        moved_pairs.update((p_id, b_id) for p_id, b_id in ti_out_qs if p_id and b_id)

        self.stdout.write(f"Found {len(moved_pairs)} product-branch pair(s) with recorded movements.")

        # 2. Fetch existing Stock objects for quick lookup
        existing_stocks = Stock.objects.filter(branch_id__in=branch_ids).select_related("product", "branch")
        stock_map = {(s.product_id, s.branch_id): s for s in existing_stocks}

        # Counters
        evaluated_pairs = 0
        updated_existing_count = 0
        created_new_count = 0
        already_exempt_count = 0
        skipped_moved_count = 0

        stocks_to_update = []
        stocks_to_create = []

        with transaction.atomic():
            for branch in branches:
                for product in products:
                    evaluated_pairs += 1
                    pair = (product.id, branch.id)

                    if pair in moved_pairs:
                        skipped_moved_count += 1
                        continue

                    # Pair has zero product movements in product trail
                    stock = stock_map.get(pair)

                    if stock:
                        if not stock.exempt_from_auto_reorder:
                            stock.exempt_from_auto_reorder = True
                            stocks_to_update.append(stock)
                            updated_existing_count += 1
                        else:
                            already_exempt_count += 1
                    else:
                        if not skip_create_missing:
                            new_stock = Stock(
                                product=product,
                                branch=branch,
                                quantity=0,
                                exempt_from_auto_reorder=True,
                            )
                            stocks_to_create.append(new_stock)
                            created_new_count += 1

            if not dry_run:
                if stocks_to_update:
                    Stock.objects.bulk_update(stocks_to_update, ["exempt_from_auto_reorder", "updated_at"])
                if stocks_to_create:
                    Stock.objects.bulk_create(stocks_to_create)
            else:
                transaction.set_rollback(True)

        self.stdout.write("\n" + self.style.SUCCESS("=== SUMMARY ==="))
        self.stdout.write(f"Total product-branch pairs evaluated: {evaluated_pairs}")
        self.stdout.write(f"Pairs skipped (have product movements): {skipped_moved_count}")
        self.stdout.write(f"Existing stock rows already exempt: {already_exempt_count}")
        self.stdout.write(
            self.style.SUCCESS(f"Existing stock rows updated to exempt: {updated_existing_count}")
        )
        self.stdout.write(
            self.style.SUCCESS(f"New stock rows created & marked exempt: {created_new_count}")
        )
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\n[DRY-RUN] {updated_existing_count + created_new_count} stock row(s) would be exempted. No DB changes saved."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nSuccessfully exempted {updated_existing_count + created_new_count} stock row(s) from auto-reorder!"
                )
            )
