from django.db import migrations, models


def copy_product_max_stock(apps, schema_editor):
    Stock = apps.get_model("products", "Stock")
    Product = apps.get_model("products", "Product")
    product_max_stocks = dict(Product.objects.values_list("id", "max_stock"))
    stocks = list(Stock.objects.all())
    for stock in stocks:
        stock.max_stock = product_max_stocks.get(stock.product_id)
    Stock.objects.bulk_update(stocks, ["max_stock"])


class Migration(migrations.Migration):
    dependencies = [("products", "0024_stock_reorder_level")]

    operations = [
        migrations.AddField(
            model_name="stock",
            name="max_stock",
            field=models.PositiveIntegerField(
                blank=True,
                default=None,
                help_text="Branch-specific maximum stock. Falls back to the product default when unset.",
                null=True,
            ),
        ),
        migrations.RunPython(copy_product_max_stock, migrations.RunPython.noop),
    ]
