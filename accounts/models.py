from django.conf import settings
from django.db import models


class SystemConfig(models.Model):
    """系统配置（单行单例）：GitCode OAuth 等原环境变量配置转移至数据库。

    - 通过 get_singleton() 获取唯一实例（不存在则创建）
    """

    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "系统配置"
        verbose_name_plural = "系统配置"

    def __str__(self):
        return "系统配置"

    @classmethod
    def get_singleton(cls):
        """获取唯一配置实例（不存在则创建）。"""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj


class EmailVerification(models.Model):
    """邮箱验证码：用于确认邮箱归属（用户改邮箱）与 SMTP 配置可用性。

    - 用户邮箱验证：user 关联当前用户，验证通过后更新用户邮箱
    - SMTP 配置验证：user 为空，验证通过后才允许配置入库
    - 验证码 6 位数字，10 分钟有效，错误超过 5 次作废
    """

    PURPOSE_USER_EMAIL = "user_email"
    PURPOSE_SMTP_CONFIG = "smtp_config"
    PURPOSE_CHOICES = [
        (PURPOSE_USER_EMAIL, "用户邮箱验证"),
        (PURPOSE_SMTP_CONFIG, "SMTP 配置验证"),
    ]

    email = models.EmailField("目标邮箱")
    code = models.CharField("验证码", max_length=6)
    purpose = models.CharField("用途", max_length=20, choices=PURPOSE_CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="email_verifications",
        verbose_name="用户",
    )
    expires_at = models.DateTimeField("过期时间")
    used = models.BooleanField("已使用", default=False)
    attempts = models.PositiveIntegerField("错误尝试次数", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "邮箱验证码"
        verbose_name_plural = "邮箱验证码"

    def __str__(self):
        return f"{self.email} {self.get_purpose_display()} ({self.code})"
