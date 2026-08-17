from django.contrib import admin

from config.admin import configured_field

from .models import EmailConfig, WebhookConfig


@admin.register(EmailConfig)
class EmailConfigAdmin(admin.ModelAdmin):
    list_display = ("host", "port", "username", "send_via", "enabled", "updated_at")
    list_filter = ("send_via", "enabled")
    readonly_fields = (
        "has_password",
        "has_mail_webhook_url",
        "has_mail_webhook_token",
        "created_at",
        "updated_at",
    )
    fields = (
        "host",
        "port",
        "username",
        "from_email",
        "use_ssl",
        "send_via",
        "enabled",
        "has_password",
        "has_mail_webhook_url",
        "has_mail_webhook_token",
        "created_at",
        "updated_at",
    )

    has_password = configured_field("password", "已配置 SMTP 密码")
    has_mail_webhook_url = configured_field("mail_webhook_url", "已配置邮件 Webhook URL")
    has_mail_webhook_token = configured_field("mail_webhook_token", "已配置邮件 Webhook Token")


@admin.register(WebhookConfig)
class WebhookConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "enabled", "updated_at")
    list_filter = ("name", "enabled")
    readonly_fields = ("has_url", "has_secret", "created_at", "updated_at")
    fields = ("name", "owner", "enabled", "has_url", "has_secret", "created_at", "updated_at")

    has_url = configured_field("url", "已配置 URL")
    has_secret = configured_field("secret", "已配置密钥")
