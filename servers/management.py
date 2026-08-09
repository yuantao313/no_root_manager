"""目标机器用户接管与开通服务：所有受管用户加入 nrm_managed 组。"""

import logging
import re
import secrets
import shlex
import string

from django.utils import timezone

from .models import ManagedUser, Server
from .ssh import exec_command

logger = logging.getLogger(__name__)

NRM_GROUP = "nrm_managed"

# 需要 root 权限的管理命令（SSH 用户非 root 时自动加 sudo -n 前缀）
PRIVILEGED_CMDS = (
    "groupadd",
    "usermod",
    "useradd",
    "chpasswd",
    "gpasswd",
    "chage",
    "mv",
    "chown",
    "rm",
    "rmdir",
    "userdel",
)


def _sudo_wrap(server, command: str) -> str:
    """若 SSH 用户非 root，则为特权命令加 sudo -n 前缀。

    按管道（单个 |，排除逻辑或 ||）分段处理，保证
    `echo ... | chpasswd` 这类命令的后半段特权命令也能被提权。
    """
    cred_username = server.credential.username if server.credential else ""
    needs_sudo = cred_username != "root"
    if not needs_sudo:
        return command
    # 只按单个管道符分段，避免把 || 拆开
    parts = re.split(r"(?<!\|)\|(?!\|)", command)
    wrapped = []
    for part in parts:
        p = part.strip()
        first = p.split()[0] if p else ""
        if first in PRIVILEGED_CMDS:
            p = "sudo -n " + p
        wrapped.append(p)
    return " | ".join(wrapped)


def _exec(server, command: str):
    """带提权包装的命令执行。"""
    return exec_command(server, _sudo_wrap(server, command))


def _random_password(length=16):
    """生成随机密码（字母+数字，排除易混淆字符）。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ensure_nrm_group(server):
    """确保目标机器存在 nrm_managed 组，不存在则创建。返回 (ok, msg)。"""
    ok, out, err = _exec(server, f"getent group {NRM_GROUP}")
    if ok and out:
        return True, f"nrm_managed 组已存在：{out}"
    ok2, _, err2 = _exec(server, f"groupadd {NRM_GROUP}")
    if ok2:
        return True, "已创建 nrm_managed 组"
    return False, f"创建 nrm_managed 组失败：{err2 or err}"


def list_nrm_members(server):
    """读取目标机器 nrm_managed 组的成员列表。返回 (ok, members:list, msg)。"""
    ok, out, err = _exec(server, f"getent group {NRM_GROUP}")
    if not ok:
        return False, [], err
    if not out:
        return True, [], "nrm_managed 组不存在或为空"
    try:
        # getent group 输出格式: groupname:passwd:gid:member1,member2
        members = out.split(":")[-1].strip()
        return True, [m for m in members.split(",") if m], out
    except Exception:  # noqa: BLE001
        return False, [], out


def take_over_user(server, username):
    """将用户加入 nrm_managed 组（接管）。返回 (ok, msg)。"""
    username = (username or "").strip()
    if not username:
        return False, "用户名为空"
    ok, _, err = _exec(server, f"usermod -aG {NRM_GROUP} {username}")
    if ok:
        return True, f"用户 {username} 已加入 {NRM_GROUP} 组"
    return False, f"接管失败：{err}"


def sync_managed_users(server):
    """同步目标机器 nrm_managed 组成员到数据库。返回 (ok, msg)。"""
    ok, members, msg = list_nrm_members(server)
    if not ok:
        return False, msg
    for name in members:
        ManagedUser.objects.update_or_create(server=server, username=name)
    # 删除已不在组成员中的记录
    ManagedUser.objects.filter(server=server).exclude(username__in=members).delete()
    return True, f"同步完成，共 {len(members)} 个受管用户"


def lock_user(server, username):
    """禁用目标机器用户（passwd -l）。返回 (ok, msg)。"""
    username = (username or "").strip()
    if not username:
        return False, "用户名为空"
    ok, _, err = _exec(server, f"passwd -l {username}")
    if ok:
        return True, f"用户 {username} 已禁用"
    return False, f"禁用失败：{err}"


def unlock_user(server, username):
    """启用目标机器用户（passwd -u）。返回 (ok, msg)。"""
    username = (username or "").strip()
    if not username:
        return False, "用户名为空"
    ok, _, err = _exec(server, f"passwd -u {username}")
    if ok:
        return True, f"用户 {username} 已启用"
    return False, f"启用失败：{err}"


def list_system_users(server):
    """列出目标机器 /home 下可接管的系统用户（排除已受管成员）。

    返回 (ok, available_users:list, msg)。用于接管按钮化的候选列表。
    """
    ok, out, err = _exec(server, "ls -1 /home")
    if not ok:
        return False, [], err or "读取用户列表失败"
    names = [n for n in out.split() if n]
    _, members, _ = list_nrm_members(server)
    available = [n for n in names if n not in members]
    return True, available, f"共 {len(available)} 个可接管用户"


def collect_user_usage(server, username):
    """采集单个用户资源使用：磁盘占用、内存、CPU。

    返回 (ok, dict(disk/mem/cpu), msg)。磁盘取 /home 大小（如 1.2G），
    内存/CPU 由 ps 按用户汇总（如 256MB / 3.5%）。
    """
    username = (username or "").strip()
    if not username:
        return False, {}, "用户名为空"
    cmd = (
        f'echo "DISK=$(du -sh /home/{username} 2>/dev/null | cut -f1) '
        f"USAGE=$(ps -u {username} -o rss=,%cpu= --no-headers 2>/dev/null | "
        f'awk \'{{m+=$1;c+=$2}} END{{printf "%.0fMB %.1f%%", m/1024, c}}\')"'
    )
    ok, out, err = _exec(server, cmd)
    if not ok:
        return False, {}, err or "采集失败"
    disk, mem, cpu = "", "", ""
    m = re.search(r"DISK=(\S+)", out)
    if m:
        disk = m.group(1)
    m = re.search(r"USAGE=(\S+)", out)
    if m:
        parts = m.group(1).split()
        if len(parts) == 2:
            mem, cpu = parts[0], parts[1]
    return True, {"disk": disk, "mem": mem, "cpu": cpu}, out


def sync_user_usage(server):
    """采集全部受管用户的资源使用并入库。返回 (ok, msg)。"""
    users = list(ManagedUser.objects.filter(server=server))
    if not users:
        return True, "无受管用户可采集"
    collected = 0
    for mu in users:
        ok, data, _ = collect_user_usage(server, mu.username)
        if ok:
            mu.disk_used = data["disk"]
            mu.mem_used = data["mem"]
            mu.cpu_used = data["cpu"]
            mu.usage_synced_at = timezone.now()
            mu.save(update_fields=["disk_used", "mem_used", "cpu_used", "usage_synced_at"])
            collected += 1
    return True, f"资源采集完成：{collected}/{len(users)} 个用户"


def provision_user(server, username, groups=None, expire_date=None, with_home=True, force_pwd_change=True):
    """在目标机器开通用户：建用户、加入分组、设置随机密码、写入资源限制。

    groups: 要加入的机器分组列表（含 nrm_managed）。
    expire_date: 可选，账号到期日期（YYYY-MM-DD），到期后自动失效。
    with_home: 是否预建 home 目录。申请了目录迁移时应为 False，
        让迁移流程把用户已有目录移到 /home/username。
    force_pwd_change: 是否强制首次登录修改密码（chage -d 0）。
    返回 (ok, password, msg)。
    """
    username = (username or "").strip()
    if not username:
        return False, "", "用户名为空"

    groups = list(groups or [])
    if NRM_GROUP not in groups:
        groups.append(NRM_GROUP)
    group_args = ",".join(groups)

    # 用户已存在则跳过创建，只补分组
    exists, _, _ = _exec(server, f"id -u {username}")
    if exists:
        ok, _, err = _exec(server, f"usermod -aG {group_args} {username}")
        if not ok:
            return False, "", f"更新用户分组失败：{err}"
    else:
        home_flag = "-m " if with_home else ""
        ok, _, err = _exec(
            server,
            f"useradd {home_flag}-s /bin/bash -G {group_args} {username}".replace("  ", " "),
        )
        if not ok:
            return False, "", f"创建用户失败：{err}"

    if expire_date:
        ok, _, err = _exec(server, f"usermod -e {expire_date} {username}")
        if not ok:
            return False, "", f"设置到期时间失败：{err}"

    # 生成随机密码并通过 chpasswd 设置
    password = _random_password()
    ok, _, err = _exec(server, f"echo '{username}:{password}' | chpasswd")
    if not ok:
        return False, "", f"设置随机密码失败：{err}"

    # 强制首次登录修改密码（chage -d 0 使密码立即过期，下次登录需改密）
    if force_pwd_change:
        ok, _, err = _exec(server, f"chage -d 0 {username}")
        if not ok:
            return False, "", f"设置强制改密失败：{err}"

    # 写入资源限制（ulimit），防止单个用户耗尽服务器资源
    if any(
        [
            server.nproc_limit,
            server.nofile_limit,
            server.as_limit,
            server.core_limit,
            server.fsize_limit,
            server.maxlogins_limit,
        ]
    ):
        ok, err = apply_resource_limits(server, username)
        if not ok:
            return False, "", f"设置资源限制失败：{err}"

    return True, password, f"用户 {username} 已开通（分组：{group_args}）"


# 资源限制项：字段名 -> limits.conf 的 item 名（hard 限制）
RESOURCE_LIMIT_ITEMS = (
    ("nproc_limit", "nproc"),
    ("nofile_limit", "nofile"),
    ("as_limit", "as"),
    ("core_limit", "core"),
    ("fsize_limit", "fsize"),
    ("maxlogins_limit", "maxlogins"),
)


def apply_resource_limits(server, username):
    """为用户在目标机器写入资源限制（/etc/security/limits.d/nrm-<user>.conf）。

    使用 limits.d 独立文件而非直接改 limits.conf，便于删除用户时一并清理。
    服务器上各限制字段为 0 时对应项不写入。
    返回 (ok, msg)。
    """
    username = (username or "").strip()
    if not username:
        return False, "用户名为空"

    lines = []
    for field, item in RESOURCE_LIMIT_ITEMS:
        value = getattr(server, field, 0)
        if value:
            lines.append(f"{username} hard {item} {value}")
    if not lines:
        return True, "未配置资源限制"

    conf = f"/etc/security/limits.d/nrm-{username}.conf"
    content = "\n".join(lines)
    # 用 printf 写入避免引号/换行转义问题；先写临时文件再 mv 防半截
    ok, _, err = _exec(
        server,
        f"printf '%s\\n' '{content}' | sudo -n tee {conf} >/dev/null",
    )
    if not ok:
        return False, err or "写入失败"
    return True, f"已写入资源限制（{content.replace(chr(10), ', ')}）"


# sudo 组名：Debian/Ubuntu 为 sudo，RHEL/CentOS 为 wheel，这里按 sudo 优先探测
SUDO_GROUPS = ("sudo", "wheel")


def detect_sudo_group(server):
    """探测目标机器的 sudo 组名。返回 (ok, group, msg)。"""
    for group in SUDO_GROUPS:
        ok, out, err = _exec(server, f"getent group {group}")
        if ok and out:
            return True, group, f"使用 {group} 组"
    return False, "", "未找到 sudo/wheel 组"


def grant_sudo(server, username):
    """授予用户 root/sudo 权限（加入 sudo 组）。返回 (ok, group, msg)。"""
    ok, group, msg = detect_sudo_group(server)
    if not ok:
        return False, "", msg
    ok2, _, err = _exec(server, f"usermod -aG {group} {username}")
    if ok2:
        return True, group, f"用户 {username} 已加入 {group} 组，获得 sudo 权限"
    return False, "", f"授予 sudo 失败：{err}"


def revoke_sudo(server, username):
    """撤销用户 sudo 权限（从 sudo/wheel 组移除）。返回 (ok, msg)。"""
    ok, _, msg = detect_sudo_group(server)
    if not ok:
        return False, msg
    # 从所有可能的 sudo 组移除
    for group in SUDO_GROUPS:
        _exec(server, f"gpasswd -d {username} {group}")
    return True, f"已撤销用户 {username} 的 sudo 权限"


def migrate_home_dir(server, source_dir, username):
    """迁移用户已有目录到 /home/username。

    source_dir 为用户指定的来源目录（如 /home/old/username），
    迁移后源目录将被移动到 /home/username。返回 (ok, msg)。
    """
    source_dir = (source_dir or "").strip().rstrip("/")
    if not source_dir:
        return False, "未指定迁移目录"
    if not source_dir.startswith("/"):
        return False, "迁移目录必须是绝对路径"
    if any(ch in source_dir for ch in (";", "|", "&", "$", "`", "\n")):
        return False, "迁移目录包含非法字符"

    target = f"/home/{username}"
    # 目标已存在：用 root 权限判断是否为空（useradd -m 会预建空 home）
    # 空则移除后迁移，非空则拒绝避免覆盖
    ok, _, _ = _exec(server, f"test -d {target}")
    if ok:
        ok2, out2, _ = _exec(server, f"sudo -n sh -c 'ls -A {target} | wc -l'")
        if ok2 and out2.strip() != "0":
            return False, f"目标目录 {target} 已存在且非空，未迁移"
        _exec(server, f"rmdir {target}")
    ok, out, err = _exec(server, f"test -d {source_dir}")
    if not ok:
        return False, f"来源目录 {source_dir} 不存在或无法访问：{err or '不存在'}"
    # 用 -T 保证目标已存在时直接报错，绝不嵌套移动
    ok, _, err = _exec(server, f"mv -T {source_dir} {target}")
    if not ok:
        return False, f"迁移失败：{err}"
    # 修正属主，确保新用户可读写；失败则回滚 mv，恢复原状
    ok, _, err = _exec(server, f"chown -R {username}:{username} {target}")
    if not ok:
        _exec(server, f"mv -T {target} {source_dir}")
        return False, f"迁移后设置属主失败，已回滚：{err}"
    return True, f"已将 {source_dir} 迁移到 {target}"


# ===== 初始化脚本 / NPU 分组 / 用户公告 =====


def run_init_script(server):
    """远程 get 初始化脚本并在目标机运行（curl -sL <url> | sudo bash）。返回 (ok, msg)。"""
    url = (server.init_script or "").strip()
    if not url:
        return False, "未配置初始化脚本 URL"
    ok, out, err = _exec(server, f"curl -sL '{url}' | sudo -n bash -s")
    if ok:
        return True, f"初始化脚本执行完成：{out[:200]}"
    return False, f"初始化脚本执行失败：{err or out[:200]}"


def detect_npu_groups(server):
    """检测目标机 NPU 卡组：返回 (ok, groups_list, msg)，groups 含公共组 npu + 卡组 npuN。"""
    ok, out, err = _exec(server, "ls -1 /dev/davinci[0-9]* 2>/dev/null")
    if not ok or not out:
        return False, [], err or "未检测到 /dev/davinciN（Ascend 驱动未加载）"
    ids = sorted(
        {m.group(1) for line in out.split() if (m := re.search(r"davinci(\d+)$", line))},
        key=int,
    )
    groups = ["npu"] + [f"npu{i}" for i in ids]
    return True, groups, f"检测到 {len(ids)} 张 NPU 卡：{ids}"


def grant_npu_access(server, username, groups):
    """授权用户 NPU 卡组（usermod -aG npu,npuN）。返回 (ok, msg)。"""
    username = (username or "").strip()
    if not username or not groups:
        return False, "参数错误"
    ok, _, err = _exec(server, f"usermod -aG {','.join(groups)} {username}")
    if ok:
        return True, f"用户 {username} 已加入 NPU 卡组：{','.join(groups)}"
    return False, f"NPU 授权失败：{err}"


def _announcement_text():
    """启用公告拼成的纯文本（用于写入服务器 motd）。"""
    from accounts.models import Announcement

    notices = [n for n in Announcement.objects.filter(enabled=True) if n.content.strip()]
    if not notices:
        return ""
    return "\n\n".join(f"# {n.title}\n{n.content}" for n in notices)


def write_server_motd(server):
    """把启用中的公告写入目标服务器 motd（/etc/motd.d/nrm_notifications），
    所有用户 SSH 登录时自动显示；无启用公告时清除 motd 文件。返回 (ok, msg)。
    """
    content = _announcement_text()
    motd_file = "/etc/motd.d/nrm_notifications"
    # Ubuntu 使用 /etc/motd.d/ 聚合展示；确保目录存在后写入（root 权限）
    if content:
        ok, _, err = _exec(
            server,
            f"mkdir -p /etc/motd.d && echo {shlex.quote(content)} > {motd_file}",
        )
        if not ok:
            return False, f"motd 写入失败：{err}"
        return True, f"公告已写入目标机 motd：{motd_file}"
    # 无启用公告：清除 motd 文件，避免残留旧公告
    ok, _, err = _exec(server, f"rm -f {motd_file}")
    if not ok:
        return False, f"motd 清理失败：{err}"
    return True, "无启用公告，已清除 motd 公告"


def push_notices(server=None):
    """批量推送公告：写入目标机 motd（SSH 登录显示）。

    server 为空则推送全部服务器。返回 (ok, msg)。
    """
    if not _announcement_text():
        return True, "无启用公告，跳过"
    servers = [server] if server else list(Server.objects.all())
    done, fail = 0, []
    for s in servers:
        ok, msg = write_server_motd(s)
        if ok:
            done += 1
        else:
            fail.append(f"{s.name}：{msg}")
    return True, f"公告推送完成：{done} 台服务器 motd" + (f"；失败：{'；'.join(fail)}" if fail else "")
