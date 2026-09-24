from django.db import migrations, models


def copy_product_reorder_levels(apps, schema_editor):
    Stock = apps.get_model("products", "Stock")
    Product = apps.get_model("products", "Product")
    product_levels = dict(Product.objects.values_list("id", "reorder_level"))
    stocks = list(Stock.objects.all())
    for stock in stocks:
        stock.reorder_level = product_levels.get(stock.product_id)
    Stock.objects.bulk_update(stocks, ["reorder_level"])


class Migration(migrations.Migration):
    dependencies = [("products", "0023_supplierreorderrequest_branch")]

    operations = [
        migrations.AddField(
            model_name="stock",
            name="reorder_level",
            field=models.IntegerField(
                blank=True,
                default=None,
                help_text="Branch-specific reorder threshold. Falls back to the product default when unset.",
                null=True,
            ),
        ),
        migrations.RunPython(copy_product_reorder_levels, migrations.RunPython.noop),
    ]
