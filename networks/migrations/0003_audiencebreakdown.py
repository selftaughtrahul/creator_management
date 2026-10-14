import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("networks", "0002_replace_linkedin_with_twitter"),
    ]

    operations = [
        migrations.CreateModel(
            name="AudienceBreakdown",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "dimension",
                    models.CharField(
                        choices=[("country", "Country"), ("gender", "Gender")],
                        max_length=20,
                    ),
                ),
                ("segment", models.CharField(max_length=80)),
                ("label", models.CharField(max_length=80)),
                ("count", models.PositiveIntegerField(default=0)),
                (
                    "basis",
                    models.CharField(
                        choices=[
                            ("followers", "Followers"),
                            ("estimated", "Estimated from viewer share"),
                        ],
                        default="followers",
                        max_length=20,
                    ),
                ),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="breakdowns",
                        to="networks.metricsnapshot",
                    ),
                ),
            ],
            options={
                "ordering": ["dimension", "-count", "label"],
            },
        ),
        migrations.AddConstraint(
            model_name="audiencebreakdown",
            constraint=models.UniqueConstraint(
                fields=("snapshot", "dimension", "segment"),
                name="unique_snapshot_segment",
            ),
        ),
    ]
