from django.conf import settings
from django.db import models

from credentials.models import Credential


class ServerAdminBinding(models.Model):
    """普通管理员与服务器的绑定关系（权限分配）。

    - 超级管理员（is_superuser）拥有全部服务器，无需绑定
    - 普通管理员（is_staff 且非 superuser）只能管理绑定表中的服务器
    - 由超级管理员在系统设置中维护
    """

    server = models.ForeignKey(
        "Server",
        on_delete=models.CASCADE,
        related_name="admin_bindings",
        verbose_name="服务器",
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="server_bindings",
        verbose_name="普通管理员",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        unique_together = ("server", "admin")
        verbose_name = "服务器管理员绑定"
        verbose_name_plural = "服务器管理员绑定"

    def __str__(self):
        return f"{self.admin.username} → {self.server.name}"


class ManagedUser(models.Model):
    """受管用户：目标机器上被 NRM 接管（加入 nrm_managed 组）的用户。"""

    server = models.ForeignKey(
        "Server",
        on_delete=models.CASCADE,
        related_name="managed_users",
        verbose_name="所属服务器",
    )
    username = models.CharField("用户名", max_length=100)
    # 机器受管用户 ↔ 系统账号一对一绑定（可选；接管/转移时指定具体用户）
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_user",
        verbose_name="绑定系统用户",
        help_text="目标机器用户对应的 NRM 账号（一对一）",
    )
    synced_at = models.DateTimeField("最近同步时间", auto_now=True)
    # 用户在目标机器上的附加分组（逗号分隔，同步时读取）
    groups = models.CharField("机器分组", max_length=500, blank=True)
    # 资源使用（同步采集时更新；空表示未采集或用户无进程/家目录）
    disk_used = models.CharField("磁盘占用", max_length=50, blank=True, default="")
    mem_used = models.CharField("内存占用", max_length=50, blank=True, default="")
    cpu_used = models.CharField("CPU 占用", max_length=50, blank=True, default="")
    usage_synced_at = models.DateTimeField("资源采集时间", null=True, blank=True)

    class Meta:
        ordering = ["username"]
        unique_together = ("server", "username")
        verbose_name = "受管用户"
        verbose_name_plural = "受管用户"

    def __str__(self):
        return f"{self.server.name} / {self.username}"


class Server(models.Model):
    """服务器：记录目标服务器的连接信息（地址、端口），管理凭据通过外键关联。"""

    name = models.CharField("服务器名称", max_length=100)
    host = models.CharField("服务器地址", max_length=255, help_text="IP 地址或域名")
    port = models.PositiveIntegerField("端口", default=22)
    # 管理凭据（用户名/密码/密钥统一存放在凭据表，这里直接选择）
    # null=True 仅为迁移方便（DB 层可空），表单层强制必选
    credential = models.ForeignKey(
        Credential,
        on_delete=models.PROTECT,
        related_name="servers",
        verbose_name="管理凭据",
        help_text="选择已有的管理凭据（用户名/密码/密钥）",
        null=True,
        blank=True,
    )
    # 用户分组配置：默认申请的分组 + 可附加申请的分组（均为逗号分隔字符串）
    default_group = models.CharField(
        "默认申请的用户分组",
        max_length=100,
        blank=True,
        default="",
        help_text="用户申请账号时默认加入的分组，多个用英文逗号分隔",
    )
    extra_groups = models.CharField(
        "可附加申请的用户分组",
        max_length=500,
        blank=True,
        default="",
        help_text="用户申请时可附加选择加入的分组，多个用英文逗号分隔",
    )
    # 资源限制（防止单个用户耗尽服务器资源，0 表示不限制；写入 limits.d）
    nproc_limit = models.PositiveIntegerField(
        "进程数限制 nproc",
        default=128,
        help_text="每个用户最大进程数，0 表示不限制",
    )
    nofile_limit = models.PositiveIntegerField(
        "文件数限制 nofile",
        default=2048,
        help_text="每个用户最大打开文件数，0 表示不限制",
    )
    as_limit = models.PositiveIntegerField(
        "虚拟内存限制 as(KB)",
        default=0,
        help_text="每个用户最大虚拟内存（KB），0 表示不限制",
    )
    core_limit = models.PositiveIntegerField(
        "核心转储限制 core(KB)",
        default=0,
        help_text="核心转储文件大小（KB），建议 0 防占磁盘，0 表示不限制",
    )
    fsize_limit = models.PositiveIntegerField(
        "文件大小限制 fsize(KB)",
        default=0,
        help_text="每个用户最大单文件大小（KB），0 表示不限制",
    )
    maxlogins_limit = models.PositiveIntegerField(
        "最大登录数 maxlogins",
        default=0,
        help_text="每个用户最大同时登录会话数，0 表示不限制",
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "服务器"
        verbose_name_plural = "服务器"

    def __str__(self):
        return f"{self.name} ({self.host}:{self.port})"

    def extra_groups_list(self):
        """解析可附加分组为列表。"""
        return [g.strip() for g in self.extra_groups.split(",") if g.strip()]

    def default_groups_list(self):
        """解析默认分组为列表。"""
        return [g.strip() for g in self.default_group.split(",") if g.strip()]

    @classmethod
    def visible_to(cls, user):
        """用户可管理的服务器：超级管理员全部，普通管理员仅绑定的。"""
        if user.is_superuser:
            return cls.objects.all()
        return cls.objects.filter(admin_bindings__admin=user)
