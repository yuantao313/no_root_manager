from django.conf import settings
from django.db import models

from servers.fields import EncryptedTextField


class SystemConfig(models.Model):
    """系统配置（单行单例）：GitCode OAuth 等原环境变量配置转移至数据库。

    - 通过 get_singleton() 获取唯一实例（不存在则创建）
    - 敏感字段（client_secret）加密落库
    - 读取时优先数据库，未配置时回退环境变量（兼容过渡）
    """

    gitcode_client_id = models.CharField("GitCode Client ID", max_length=200, blank=True, default="")
    gitcode_client_secret = EncryptedTextField(
        "GitCode Client Secret", blank=True, help_text="存储时加密，页面不展示明文"
    )
    gitcode_scope = models.CharField("GitCode Scope", max_length=200, blank=True, default="all_user")

    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "系统配置"
        verbose_name_plural = "系统配置"

    def __str__(self):
        return "系统配置（GitCode OAuth）"

    @classmethod
    def get_singleton(cls):
        """获取唯一配置实例（不存在则创建）。"""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    @classmethod
    def gitcode_config(cls):
        """返回 GitCode OAuth 配置：数据库优先，环境变量兜底。"""
        obj = cls.objects.first()
        return {
            "client_id": (obj.gitcode_client_id if obj and obj.gitcode_client_id
                          else settings.GITCODE_CLIENT_ID),
            "client_secret": (obj.gitcode_client_secret if obj and obj.gitcode_client_secret
                              else settings.GITCODE_CLIENT_SECRET),
            "scope": (obj.gitcode_scope if obj and obj.gitcode_scope else "all_user"),
        }


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


class GitCodeBinding(models.Model):
    """用户与 GitCode 账号的绑定关系（用户 id 映射，不修改 auth 用户模型）。

    - 已注册用户可主动绑定自己的 GitCode 账号
    - GitCode OAuth 登录创建的用户（gc<id>）也通过本表记录映射
    - gitcode_id 唯一：一个 GitCode 账号只能绑定一个系统用户
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gitcode_binding",
        verbose_name="用户",
    )
    gitcode_id = models.CharField(
        "GitCode 用户 id",
        max_length=100,
        unique=True,
        help_text="GitCode 用户唯一 id（24 位十六进制字符串，非数字），作为映射依据（login 可变，id 不变）",
    )
    gitcode_username = models.CharField(
        "GitCode 用户名", max_length=100, blank=True, help_text="仅展示用"
    )
    created_at = models.DateTimeField("绑定时间", auto_now_add=True)

    class Meta:
        verbose_name = "GitCode 绑定"
        verbose_name_plural = "GitCode 绑定"

    def __str__(self):
        return f"{self.user.username} → gitcode#{self.gitcode_id}"
