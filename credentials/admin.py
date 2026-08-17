from django.contrib import admin

from .models import Credential


@admin.register(Credential)
class CredentialAdmin(admin.ModelAdmin):
    list_display = ("name", "username", "has_password", "has_private_key", "updated_at")
    list_filter = ("username",)
    search_fields = ("name", "username", "remark")
    readonly_fields = ("has_password", "has_private_key", "created_at", "updated_at")
    fields = (
        "name",
        "username",
        "remark",
        "has_password",
        "has_private_key",
        "created_at",
        "updated_at",
    )

    @admin.display(description="密码", boolean=True)
    def has_password(self, obj):
        return bool(obj.password)

    @admin.display(description="私钥", boolean=True)
    def has_private_key(self, obj):
        return bool(obj.private_key)
