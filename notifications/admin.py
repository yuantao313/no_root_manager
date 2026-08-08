from django.contrib import admin

from .models import EmailConfig, WebhookConfig


@admin.register(EmailConfig)
class EmailConfigAdmin(admin.ModelAdmin):
    list_display = ("host", "port", "username", "from_email", "enabled", "updated_at")
    list_filter = ("enabled",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(WebhookConfig)
class WebhookConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "enabled", "updated_at")
    list_filter = ("enabled",)
    readonly_fields = ("created_at", "updated_at")
