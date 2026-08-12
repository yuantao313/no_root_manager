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
        "nproc_limit",
        "nofile_limit",
        "updated_at",
    )
    list_filter = ("port",)
    search_fields = ("name", "host", "credential__name", "credential__username")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            None,
            {"fields": ("name", "host", "port", "credential", "default_group", "is_npu", "npu_groups", "init_script")},
        ),
        (
            "资源限制（高级设置）",
            {
                "fields": (
                    "nproc_limit",
                    "nofile_limit",
                    "as_limit",
                    "core_limit",
                    "fsize_limit",
                    "maxlogins_limit",
                )
            },
        ),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )
