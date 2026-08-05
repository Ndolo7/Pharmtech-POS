from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0019_autoreorderrequest_approval_notes_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="stock",
            name="exempt_from_auto_reorder",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Set automatically by the auto-reorder scan when this product has not sold "
                    "at this branch for at least the configured stale period. "
                    "Cleared automatically when a sale is recorded at this branch."
                ),
            ),
        ),
    ]
