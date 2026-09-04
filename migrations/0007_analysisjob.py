from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("agent", "0006_promptlog_metering")]

    operations = [
        migrations.CreateModel(
            name="AnalysisJob",
            fields=[
                ("key", models.CharField(max_length=200, primary_key=True, serialize=False)),
                ("fingerprint", models.CharField(blank=True, db_index=True, default="", max_length=200)),
                ("status", models.CharField(blank=True, db_index=True, default="", max_length=16)),
                ("document", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
            ],
            options={"db_table": "agent_analysis_job", "ordering": ["-updated_at"]},
        ),
    ]
