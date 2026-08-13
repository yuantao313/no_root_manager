from django.conf import settings
from django.db import models

from servers.fields import EncryptedTextField
from servers.models import Server


class Application(models.Model):
    """申请单：用户提交账号/权限申请，管理员审批。"""

    class ApplyType(models.TextChoices):
        CREATE = "create", "申请服务器账号"
        TRANSFER = "transfer", "转移已有账号为受管用户"
        GROUP = "group", "申请用户组"
        ADMIN = "admin", "申请平台管理员"

    # 可申请的用户组（用户组类型可选加入；逗号分隔存储）。
    # 仅普通用户组：不给普通用户开放 HwHiAiUser 等驱动专用组
    USER_GROUP_CHOICES = ["sudo", "docker"]

    class Status(models.TextChoices):
        PENDING = "pending", "待审批"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已驳回"
        WITHDRAWN = "withdrawn", "已撤回"

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
        blank=True,
        related_name="applications",
        verbose_name="目标服务器",
        help_text="从服务器列表中选择",
    )
    # 用户勾选的附加分组（来自服务器 extra_groups，逗号分隔）
    applied_groups = models.CharField(
        "附加分组",
        max_length=500,
        blank=True,
        default="",
        help_text="用户申请时勾选的可附加分组，逗号分隔",
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

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "申请单"
        verbose_name_plural = "申请单"

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    def npu_groups_display(self) -> str:
        """NPU 卡组展示值：过滤公共组 npu，只返回用户实际所选卡组。

        公共组 npu 由后端授权时自动附带，不对用户/管理员暴露"用户组"概念。
        """
        groups = [g.strip() for g in (self.applied_groups or "").split(",") if g.strip()]
        return ",".join(g for g in groups if g != "npu")
