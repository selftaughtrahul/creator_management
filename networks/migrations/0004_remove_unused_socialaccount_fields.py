from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("networks", "0003_audiencebreakdown"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="socialaccount",
            name="raw_profile",
        ),
        migrations.RemoveField(
            model_name="socialaccount",
            name="resource_token",
        ),
        migrations.RemoveField(
            model_name="socialaccount",
            name="scopes",
        ),
    ]
