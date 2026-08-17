from django.contrib import admin

from config.admin import configured_field

from .forms import CredentialForm
from .models import Credential


@admin.register(Credential)
class CredentialAdmin(admin.ModelAdmin):
    form = CredentialForm
    list_display = ("name", "username", "has_password", "has_private_key", "updated_at")
    list_filter = ("username",)
    search_fields = ("name", "username", "remark")
    readonly_fields = ("has_password", "has_private_key", "created_at", "updated_at")
    fields = (
        "name",
        "username",
        "remark",
        "password",
        "private_key",
        "has_password",
        "has_private_key",
        "created_at",
        "updated_at",
    )

    has_password = configured_field("password", "密码")
    has_private_key = configured_field("private_key", "私钥")
