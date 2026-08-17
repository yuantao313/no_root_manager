from django.contrib import admin

from .forms import SERVER_EDIT_FIELDS, ServerForm
from .models import Server


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    form = ServerForm
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
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            None,
            {"fields": SERVER_EDIT_FIELDS},
        ),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )
