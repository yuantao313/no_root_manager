from django.conf import settings
from django.db import models

from servers.fields import EncryptedTextField


class EmailConfig(models.Model):
    """SMTP 邮件配置（系统中仅一条生效记录）。"""

    host = models.CharField("SMTP 服务器", max_length=255)
    port = models.PositiveIntegerField("端口", default=465)
    username = models.CharField("用户名", max_length=255)
    password = EncryptedTextField("密码/授权码", blank=True, help_text="存储时加密")
    from_email = models.CharField("发件人地址", max_length=255, blank=True, help_text="留空则使用用户名")
    # 加密方式：use_ssl=True 为 SSL 直连（465 端口），use_ssl=False 为 STARTTLS（587/25）
    use_ssl = models.BooleanField(
        "使用 SSL 直连", default=True, help_text="465 端口为 SSL 直连；587/25 端口请取消勾选（STARTTLS）"
    )
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
    """

    name = models.CharField("名称", max_length=100)
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
