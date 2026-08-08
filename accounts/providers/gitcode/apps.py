from django.apps import AppConfig


class GitCodeProviderConfig(AppConfig):
    name = "accounts.providers.gitcode"
    label = "gitcode_provider"
    verbose_name = "GitCode OAuth Provider"
