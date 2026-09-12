"""The checkpoint table, plus the ledger's new ``cached`` cost basis.

Additive only: a new table nothing reads yet, and one more choice on an
existing column (choices are validation, not storage — no data moves).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("agent", "0007_analysisjob")]

    operations = [
        migrations.CreateModel(
            name="ProviderCheckpoint",
            fields=[
                ("key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("surface", models.CharField(db_index=True, max_length=32)),
                ("provider", models.CharField(blank=True, default="", max_length=128)),
                ("value", models.JSONField(blank=True, default=dict)),
                ("user_id", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("workspace_id", models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "db_table": "agent_provider_checkpoint",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["surface", "-created_at"],
                        name="agent_ckpt_surface_idx",
                    )
                ],
            },
        ),
        migrations.AlterField(
            model_name="promptlog",
            name="cost_basis",
            field=models.CharField(
                blank=True,
                choices=[
                    ("provider_ticks", "Reported by the provider"),
                    ("pricing_estimate", "Estimated from the rate card"),
                    ("unpriced", "No rate card — cost unknown"),
                    ("cached", "Served from a checkpoint — no provider call"),
                ],
                max_length=16,
                null=True,
            ),
        ),
    ]
