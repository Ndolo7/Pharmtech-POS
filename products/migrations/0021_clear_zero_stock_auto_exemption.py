from django.db import migrations


def fix_wrongly_exempted_unstocked_products(apps, schema_editor):
    Stock = apps.get_model("products", "Stock")
    StockMovement = apps.get_model("products", "StockMovement")
    PurchaseItem = apps.get_model("products", "PurchaseItem")
    TransferItem = apps.get_model("products", "TransferItem")

    candidate_stocks = Stock.objects.filter(quantity__lte=0, exempt_from_auto_reorder=True)

    unexempt_ids = []
    for stock in candidate_stocks:
        has_movements = StockMovement.objects.filter(
            product_id=stock.product_id,
            branch_id=stock.branch_id,
            quantity__gt=0,
        ).exists()

        has_purchases = PurchaseItem.objects.filter(
            product_id=stock.product_id,
            purchase__branch_id=stock.branch_id,
        ).exists()

        has_transfers_in = TransferItem.objects.filter(
            product_id=stock.product_id,
            transfer__to_branch_id=stock.branch_id,
        ).exists()

        # Only un-exempt if the product was NEVER stocked/received at this branch
        if not (has_movements or has_purchases or has_transfers_in):
            unexempt_ids.append(stock.id)

    if unexempt_ids:
        Stock.objects.filter(id__in=unexempt_ids).update(exempt_from_auto_reorder=False)


def reverse_fix_wrongly_exempted_unstocked_products(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0020_stock_exempt_from_auto_reorder"),
    ]

    operations = [
        migrations.RunPython(
            fix_wrongly_exempted_unstocked_products,
            reverse_fix_wrongly_exempted_unstocked_products,
        ),
    ]
