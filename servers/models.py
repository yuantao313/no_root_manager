from django.conf import settings
from django.db import models

from credentials.models import Credential

ROOT_EQUIVALENT_GROUPS = frozenset({"sudo", "wheel", "docker"})


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


class MachineUserBinding(models.Model):
    """机器用户 ↔ 平台用户归属绑定（数据库记录，不单独做前端展示）。

    所有受 NRM 监管（nrm_managed 组）的用户都在系统里有记录：
    - 转移/创建/管理员/用户组类型审批通过时自动写入（机器用户 → 申请人）
    - 管理员手动接管时可选写入
    - server+username 唯一：同一机器用户只归属一个平台用户（防重复接管）
    """

    server = models.ForeignKey(
        "Server",
        on_delete=models.CASCADE,
        related_name="machine_user_bindings",
        verbose_name="服务器",
    )
    username = models.CharField("机器用户名", max_length=100)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="machine_user_bindings",
        verbose_name="归属平台用户",
    )
    source = models.CharField(
        "来源",
        max_length=20,
        default="transfer",
        choices=[
            ("transfer", "转移接管"),
            ("manual", "手动接管"),
            ("create", "创建开通"),
            ("admin", "平台管理员"),
            ("group", "用户组"),
        ],
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        unique_together = ("server", "username")
        ordering = ["server", "username"]
        verbose_name = "机器用户绑定"
        verbose_name_plural = "机器用户绑定"

    def __str__(self):
        owner = self.user.username if self.user else "未绑定"
        return f"{self.server.name} / {self.username} → {owner}"


class Server(models.Model):
    """服务器：记录目标服务器的连接信息（地址、端口），管理凭据通过外键关联。"""

    name = models.CharField("服务器名称", max_length=100)
    host = models.CharField("服务器地址", max_length=255, help_text="IP 地址或域名")
    port = models.PositiveIntegerField("端口", default=22)
    ssh_host_key_fingerprint = models.CharField(
        "SSH 主机指纹",
        max_length=80,
        blank=True,
        default="",
        help_text=(
            "OpenSSH SHA256 指纹。首次请选“保存并测试连接”获取候选指纹，"
            "通过可信渠道核对后填入；指纹变化时系统将拒绝连接。"
        ),
    )
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
    # 新账号默认加入的用户分组（逗号分隔字符串）
    default_group = models.CharField(
        "默认申请的用户分组",
        max_length=100,
        blank=True,
        default="",
        help_text="用户申请账号时默认加入的分组，多个用英文逗号分隔",
    )
    # 设备信息快照（最近一次成功查询结果落库）：目标机不可达时回退展示，避免页面空白。
    # 结构同 servers/devices.py 的 get_device_info 返回：{cpu, memory, disk}
    device_info_snapshot = models.JSONField(
        "设备信息快照",
        blank=True,
        default=dict,
        help_text="最近一次成功采集的设备信息（CPU/内存/硬盘），查询失败时回退展示",
    )
    device_info_updated_at = models.DateTimeField("设备信息更新时间", null=True, blank=True)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "服务器"
        verbose_name_plural = "服务器"

    def __str__(self):
        return f"{self.name} ({self.host}:{self.port})"

    def default_groups_list(self):
        """解析默认分组为列表。"""
        return [g.strip() for g in self.default_group.split(",") if g.strip()]

    @classmethod
    def visible_to(cls, user):
        """用户可管理的服务器：超级管理员全部，普通管理员仅绑定的。"""
        if user.is_superuser:
            return cls.objects.all()
        return cls.objects.filter(admin_bindings__admin=user)
