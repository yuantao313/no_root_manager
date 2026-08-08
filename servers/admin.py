from django.contrib import admin

from .models import ManagedUser, Server


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ("name", "host", "port", "credential", "default_group", "extra_groups", "nproc_limit", "nofile_limit", "updated_at")
    list_filter = ("port",)
    search_fields = ("name", "host", "credential__name", "credential__username")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "host", "port", "credential", "default_group", "extra_groups")}),
        ("资源限制（高级设置）", {"fields": (
            "nproc_limit", "nofile_limit", "as_limit", "core_limit", "fsize_limit", "maxlogins_limit",
        )}),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(ManagedUser)
class ManagedUserAdmin(admin.ModelAdmin):
    list_display = ("username", "server", "groups", "synced_at")
    search_fields = ("username", "server__name")
    readonly_fields = ("synced_at",)
