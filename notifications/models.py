from django.conf import settings
from django.db import models

from servers.fields import EncryptedTextField


class EmailConfig(models.Model):
    """SMTP 邮件配置（系统中仅一条生效记录）。

    发送方式 send_via 显式二选一（smtp / webhook），不自动降级；
    邮件 Webhook 用于规避 SMTP 端口屏蔽（25/465/587 被禁时走 443 HTTPS）。
    """

    # 发送方式：smtp=直连 SMTP（原逻辑）；webhook=邮件 Webhook（独立配置）
    SEND_VIA_SMTP = "smtp"
    SEND_VIA_WEBHOOK = "webhook"
    SEND_VIA_CHOICES = [
        (SEND_VIA_SMTP, "SMTP 直连"),
        (SEND_VIA_WEBHOOK, "邮件 Webhook"),
    ]

    host = models.CharField("SMTP 服务器", max_length=255)
    port = models.PositiveIntegerField("端口", default=465)
    username = models.CharField("用户名", max_length=255)
    password = EncryptedTextField("密码/授权码", blank=True, help_text="存储时加密")
    from_email = models.CharField("发件人地址", max_length=255, blank=True, help_text="留空则使用用户名")
    # 加密方式：use_ssl=True 为 SSL 直连（465 端口），use_ssl=False 为 STARTTLS（587/25）
    use_ssl = models.BooleanField(
        "使用 SSL 直连", default=True, help_text="465 端口为 SSL 直连；587/25 端口请取消勾选（STARTTLS）"
    )
    # 发送方式（显式选择，不自动降级）
    send_via = models.CharField("发送方式", max_length=10, choices=SEND_VIA_CHOICES, default=SEND_VIA_SMTP)
    # 邮件 Webhook（发送方式为 webhook 时使用）：POST {to,subject,body} + X-Webhook-Token
    mail_webhook_url = EncryptedTextField("邮件 Webhook URL", blank=True, help_text="如 http://host/webhook/mail")
    mail_webhook_token = EncryptedTextField("邮件 Webhook Token", blank=True, help_text="请求头 X-Webhook-Token")
    enabled = models.BooleanField("启用邮件通知", default=False)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "邮件配置"
        verbose_name_plural = "邮件配置"

    def __str__(self):
        return f"SMTP {self.host}:{self.port}（{'启用' if self.enabled else '停用'}）"

    def save(self, *args, **kwargs):
        # 保证只有一条生效配置：保存时把其他记录设为停用
        super().save(*args, **kwargs)
        if self.enabled:
            EmailConfig.objects.filter(enabled=True).exclude(pk=self.pk).update(enabled=False)


class WebhookConfig(models.Model):
    """Webhook 通知配置：申请事件推送 JSON 到指定 URL。

    owner 为空表示全局 Webhook（所有事件推送），
    非空表示管理员个人的 Webhook（仅本人可管理）。
    name 字段存"平台"：feishu=飞书（可读文本消息），generic=通用（原始 JSON 事件）。
    """

    PLATFORM_FEISHU = "feishu"
    PLATFORM_GENERIC = "generic"
    PLATFORM_CHOICES = [
        (PLATFORM_FEISHU, "飞书"),
        (PLATFORM_GENERIC, "通用（原始 JSON）"),
    ]

    name = models.CharField("平台", max_length=100, choices=PLATFORM_CHOICES, default=PLATFORM_FEISHU)
    url = models.URLField("Webhook URL", help_text="收到事件时推送 JSON 的地址")
    secret = EncryptedTextField("密钥", blank=True, help_text="可选，用于鉴权（存储时加密）")
    enabled = models.BooleanField("启用", default=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="webhooks",
        verbose_name="所属管理员",
        help_text="留空为全局 Webhook",
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "Webhook 配置"
        verbose_name_plural = "Webhook 配置"

    def __str__(self):
        scope = "全局" if self.owner is None else f"{self.owner.username}"
        return f"{self.name}（{scope}，{'启用' if self.enabled else '停用'}）"

    def save(self, *args, **kwargs):
        # 单例：每个作用域（全局 / 每个管理员）只保留一条，保存后清理同 owner 的旧记录
        super().save(*args, **kwargs)
        WebhookConfig.objects.filter(owner=self.owner).exclude(pk=self.pk).delete()
