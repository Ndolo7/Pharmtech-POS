from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0017_fix_legacy_sunday_time_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="autoreorderrequest",
            name="preferred_supplier_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="autoreorderrequest",
            name="unregistered_supplier_name",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="product",
            name="exempt_from_auto_reorder",
            field=models.BooleanField(default=False),
        ),
    ]
