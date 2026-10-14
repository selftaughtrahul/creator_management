from django.db import migrations, models


def promote_legacy_team(apps, schema_editor):
    StudioProfile = apps.get_model("accounts", "StudioProfile")
    StudioProfile.objects.filter(role="team").update(role="admin")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_studioprofile_theme"),
    ]

    operations = [
        migrations.RunPython(promote_legacy_team, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="studioprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("admin", "Admin"),
                    ("employee", "Employee"),
                    ("creator", "Creator"),
                ],
                default="employee",
                max_length=20,
            ),
        ),
    ]
