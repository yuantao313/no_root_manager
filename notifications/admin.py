from django.contrib import admin

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

    @admin.display(description="已配置 SMTP 密码", boolean=True)
    def has_password(self, obj):
        return bool(obj and obj.password)

    @admin.display(description="已配置邮件 Webhook URL", boolean=True)
    def has_mail_webhook_url(self, obj):
        return bool(obj and obj.mail_webhook_url)

    @admin.display(description="已配置邮件 Webhook Token", boolean=True)
    def has_mail_webhook_token(self, obj):
        return bool(obj and obj.mail_webhook_token)


@admin.register(WebhookConfig)
class WebhookConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "enabled", "updated_at")
    list_filter = ("name", "enabled")
    readonly_fields = ("has_url", "has_secret", "created_at", "updated_at")
    fields = ("name", "owner", "enabled", "has_url", "has_secret", "created_at", "updated_at")

    @admin.display(description="已配置 URL", boolean=True)
    def has_url(self, obj):
        return bool(obj and obj.url)

    @admin.display(description="已配置密钥", boolean=True)
    def has_secret(self, obj):
        return bool(obj and obj.secret)
