from django.conf import settings
from django.db import models

from servers.models import Server


class Application(models.Model):
    """申请单：用户提交账号/权限申请，管理员审批。"""

    class ApplyType(models.TextChoices):
        ACCOUNT = "account", "开通服务器账号"
        PERMISSION = "permission", "申请权限"
        OTHER = "other", "其他"

    class Status(models.TextChoices):
        PENDING = "pending", "待审批"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已驳回"

    # 申请人（登录用户，地位平等；身份信息见 applicant_name/contact）
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="applications",
        verbose_name="申请人账号",
        null=True,
        blank=True,
    )
    # 申请时填写的身份信息
    applicant_name = models.CharField("申请人姓名", max_length=50, default="")
    username = models.CharField("用户名", max_length=50, default="")
    email = models.EmailField("邮箱", max_length=100, default="")
    employee_id = models.CharField("工号", max_length=50, default="")
    apply_type = models.CharField(
        "申请类型",
        max_length=20,
        choices=ApplyType.choices,
        default=ApplyType.ACCOUNT,
    )
    title = models.CharField("申请标题", max_length=100)
    # 申请内容（账号/权限的具体说明）
    description = models.TextField("申请内容", blank=True)
    # 使用截止时间：到期后账号在目标机器自动失效
    valid_until = models.DateTimeField("使用截止时间", null=True, blank=True, help_text="到期后账号自动停用，可不填")
    # 申请 root/sudo 权限（当天有效，次日失效需重新申请）
    needs_sudo = models.BooleanField("申请 root/sudo 权限", default=False, help_text="该权限当天有效，次日自动失效")
    # 迁移来源目录：开通时从该路径迁移到 /home/username
    migrate_from_dir = models.CharField(
        "迁移来源目录",
        max_length=500,
        blank=True,
        help_text="可选，例如 /home/old/username：开通账号时自动迁移该目录到 /home/username",
    )
    # 目标服务器（从服务器表选择）
    target_server = models.ForeignKey(
        Server,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applications",
        verbose_name="目标服务器",
        help_text="从服务器列表中选择",
    )

    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    # 机器账号开通记录（审批通过后自动开通时填充）
    provisioned_at = models.DateTimeField("账号开通时间", null=True, blank=True)
    provision_note = models.TextField("开通结果", blank=True)
    # sudo 权限授予结果（独立记录，避免与开通信息混在一起）
    sudo_note = models.TextField("sudo 授予结果", blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_applications",
        verbose_name="审批人",
    )
    review_comment = models.TextField("审批意见", blank=True)
    reviewed_at = models.DateTimeField("审批时间", null=True, blank=True)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "申请单"
        verbose_name_plural = "申请单"

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


class SudoGrant(models.Model):
    """root/sudo 权限授予记录（严格审计）：当天有效，次日自动失效。"""

    class Status(models.TextChoices):
        ACTIVE = "active", "生效中"
        EXPIRED = "expired", "已失效"

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="sudo_grants",
        verbose_name="关联申请",
    )
    server = models.ForeignKey(
        Server,
        on_delete=models.CASCADE,
        related_name="sudo_grants",
        verbose_name="目标服务器",
    )
    username = models.CharField("机器用户名", max_length=100)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_sudo",
        verbose_name="授予人（审批人）",
    )
    granted_at = models.DateTimeField("授予时间", auto_now_add=True)
    expires_at = models.DateTimeField("失效时间", help_text="当日 23:59:59 失效")
    revoked_at = models.DateTimeField("实际失效时间", null=True, blank=True)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    revoke_note = models.TextField("失效说明", blank=True)

    class Meta:
        ordering = ["-granted_at"]
        verbose_name = "sudo 权限授予记录"
        verbose_name_plural = "sudo 权限授予记录"

    def __str__(self):
        return f"{self.username}@{self.server.name} sudo（{self.get_status_display()}）"
