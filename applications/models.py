from django.conf import settings
from django.db import models
from django.db.models import Q

from servers.fields import EncryptedTextField
from servers.models import ROOT_EQUIVALENT_GROUPS, Server


class ApplicationQuerySet(models.QuerySet):
    """集中定义工单的查看与审批权限范围。"""

    def reviewable_by(self, user):
        if user.is_superuser:
            return self
        if not user.is_staff:
            return self.none()
        return self.filter(target_server__admin_bindings__admin=user)

    def visible_to(self, user):
        if user.is_superuser:
            return self
        if not user.is_staff:
            return self.filter(applicant=user)
        reviewable = self.reviewable_by(user).values("pk")
        return self.filter(Q(applicant=user) | Q(pk__in=reviewable))


class Application(models.Model):
    """申请单：用户提交账号/权限申请，管理员审批。"""

    class ApplyType(models.TextChoices):
        CREATE = "create", "申请服务器账号"
        TRANSFER = "transfer", "转移已有账号为受管用户"
        GROUP = "group", "申请用户组"
        ADMIN = "admin", "申请平台管理员"

    # 可申请的高危权限组；sudo/docker 都能取得 root 级能力，只允许超级管理员审批。
    USER_GROUP_CHOICES = ["sudo", "docker"]
    PRIVILEGED_GROUPS = ROOT_EQUIVALENT_GROUPS

    class Status(models.TextChoices):
        PENDING = "pending", "待审批"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已驳回"
        WITHDRAWN = "withdrawn", "已撤回"

    BLOCKING_STATUSES = (Status.PENDING, Status.APPROVED)

    # 申请人（登录用户，地位平等；身份信息见 applicant_name/contact）
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="applications",
        verbose_name="申请人账号",
        null=True,
        blank=True,
    )
    # 提交时从平台账号复制的身份快照
    applicant_name = models.CharField("申请人姓名", max_length=50, default="")
    username = models.CharField("用户名", max_length=50, default="")
    email = models.EmailField("邮箱", max_length=100, default="")
    employee_id = models.CharField("工号", max_length=50, default="")
    apply_type = models.CharField(
        "申请类型",
        max_length=20,
        choices=ApplyType.choices,
        default=ApplyType.CREATE,
    )
    title = models.CharField("申请标题", max_length=100, blank=True, default="")
    # 申请内容（账号/权限的具体说明）
    description = models.TextField("申请内容", blank=True)
    # 目标服务器（从服务器表选择）
    target_server = models.ForeignKey(
        Server,
        on_delete=models.SET_NULL,
        null=True,
        related_name="applications",
        verbose_name="目标服务器",
        help_text="请选择申请要操作的目标服务器",
    )
    # 申请的用户组（sudo/docker，逗号分隔；创建类型可选）
    user_groups = models.CharField(
        "申请用户组",
        max_length=100,
        blank=True,
        default="",
        help_text="可申请加入的用户组：sudo、docker（逗号分隔）",
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
    # 初始密码（加密存储）：邮件不可用时可从工单查看，首次登录强制修改
    initial_password = EncryptedTextField("初始密码", blank=True)
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

    objects = ApplicationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "申请单"
        verbose_name_plural = "申请单"
        constraints = [
            models.UniqueConstraint(
                fields=["target_server", "username"],
                condition=Q(status="pending") | Q(status="approved", provisioned_at__isnull=True),
                name="uniq_active_server_username",
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    def requested_user_groups(self) -> set[str]:
        """返回规范化的用户组申请集合。"""
        return {group.strip() for group in (self.user_groups or "").split(",") if group.strip()}

    @property
    def requires_superuser_approval(self) -> bool:
        """sudo、docker 及服务器管理员授权必须由超级管理员批准。"""
        if self.apply_type == self.ApplyType.ADMIN:
            return True
        if self.requested_user_groups() & self.PRIVILEGED_GROUPS:
            return True
        return bool(
            self.apply_type == self.ApplyType.CREATE
            and self.target_server
            and set(self.target_server.default_groups_list()) & self.PRIVILEGED_GROUPS
        )
