from django.contrib import admin

from .models import Server


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "host",
        "port",
        "credential",
        "default_group",
        "updated_at",
    )
    list_filter = ("port",)
    search_fields = ("name", "host", "credential__name", "credential__username")
    readonly_fields = ("created_at", "updated_at", "npu_groups")
    fieldsets = (
        (
            None,
            {"fields": ("name", "host", "port", "credential", "default_group", "is_npu", "npu_groups")},
        ),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )
