from django.db import migrations, models


def delete_linkedin(apps, schema_editor):
    SocialAccount = apps.get_model("networks", "SocialAccount")
    SocialAccount.objects.filter(platform="linkedin").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("networks", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(delete_linkedin, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="socialaccount",
            name="platform",
            field=models.CharField(
                choices=[
                    ("instagram", "Instagram"),
                    ("facebook", "Facebook"),
                    ("youtube", "YouTube"),
                    ("twitter", "X"),
                ],
                max_length=20,
            ),
        ),
    ]
