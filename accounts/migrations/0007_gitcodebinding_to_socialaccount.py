from django.db import migrations


def migrate_to_socialaccount(apps, schema_editor):
    """将自研 GitCodeBinding 迁移为 allauth SocialAccount（provider='gitcode'）。"""
    GitCodeBinding = apps.get_model("accounts", "GitCodeBinding")
    SocialAccount = apps.get_model("socialaccount", "SocialAccount")
    for binding in GitCodeBinding.objects.all():
        SocialAccount.objects.get_or_create(
            user=binding.user,
            provider="gitcode",
            uid=str(binding.gitcode_id),
            defaults={"extra_data": {}},
        )


def reverse_migration(apps, schema_editor):
    SocialAccount = apps.get_model("socialaccount", "SocialAccount")
    SocialAccount.objects.filter(provider="gitcode").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_delete_loginlog"),
        ("socialaccount", "0006_alter_socialaccount_extra_data"),
    ]

    operations = [
        migrations.RunPython(migrate_to_socialaccount, reverse_migration),
        migrations.DeleteModel("GitCodeBinding"),
    ]
