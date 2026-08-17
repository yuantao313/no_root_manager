from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """用户扩展资料（OneToOne 关联 auth 用户，不自定义用户模型）。

    存放入职工号等申请工单复用信息；姓名用 first_name、用户名用
    User.username，申请时均从账号自动带入，无需重复填写。
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="用户",
    )
    employee_id = models.CharField("工号", max_length=50, blank=True, default="")

    class Meta:
        verbose_name = "用户扩展资料"
        verbose_name_plural = "用户扩展资料"

    def __str__(self):
        return f"{self.user.username}（{self.employee_id or '未填工号'}）"


class Announcement(models.Model):
    """用户公告（单例）：markdown 子集文本，保存后由后端转换器渲染。

    - ``content`` 为 markdown 源码（# 标题 / **加粗** / *斜体* / {颜色} / [链接](url)）
    - 系统首页公告栏用 ``markdown_to_html`` 渲染为 HTML
    - 服务器 motd 用 ``markdown_to_ansi`` 渲染为终端 ANSI 彩色文本
    系统仅保留一条公告：保存时自动清理其他记录。
    """

    content = models.TextField(
        "内容",
        blank=True,
        help_text="markdown 子集：支持 # 标题、**加粗**、*斜体*、{red}颜色{/red}、[链接](url)",
    )
    enabled = models.BooleanField("启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "用户公告"
        verbose_name_plural = "用户公告"

    def __str__(self):
        return (self.content or "（空公告）")[:40]

    def save(self, *args, **kwargs):
        # 单例：系统只保留一条公告，保存后清理其他记录
        super().save(*args, **kwargs)
        Announcement.objects.exclude(pk=self.pk).delete()

    @property
    def content_html(self) -> str:
        """markdown 子集 → HTML（首页公告栏渲染，模板 |safe 使用）。"""
        from .markdown_convert import markdown_to_html

        return markdown_to_html(self.content)


class SystemConfig(models.Model):
    """系统配置（单行单例）：GitCode OAuth 等原环境变量配置转移至数据库。

    - 通过 get_singleton() 获取唯一实例（不存在则创建）
    """

    gitcode_enabled = models.BooleanField("启用 GitCode 登录", default=True)
    # 站点基准地址（含协议/域名/端口）：GitCode 回调与 webhook 审批链接统一使用，
    # 留空时回退 settings.GITCODE_CALLBACK_BASE_URL
    site_base_url = models.CharField("站点地址", max_length=255, blank=True, default="")
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

    def get_site_base_url(self) -> str:
        """站点基准地址（GitCode 回调 / webhook 审批链接统一使用）。

        优先取数据库配置 site_base_url，留空时回退 settings.GITCODE_CALLBACK_BASE_URL。
        均未配置时返回空串（调用方自行处理）。
        """
        from django.conf import settings

        base = (self.site_base_url or "").strip().rstrip("/")
        if base:
            return base
        fallback = getattr(settings, "GITCODE_CALLBACK_BASE_URL", "").strip().rstrip("/")
        return fallback


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
    # 复用 Django 密码哈希格式，数据库泄露时不暴露仍在有效期内的 6 位验证码。
    code = models.CharField("验证码哈希", max_length=128)
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
        return f"{self.email} {self.get_purpose_display()}"
