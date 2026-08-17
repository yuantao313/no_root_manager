"""目标机器用户接管与开通服务：所有受管用户加入 nrm_managed 组。

常用服务器操作（建用户/接管/锁定/解锁/sudo 授权）统一收敛到
servers/scripts/nrm_mgmt.sh 脚本，经 SFTP 上传目标机后以 root 执行，
不再在 Python 中散落字符串命令。
"""

import re
import shlex
from pathlib import Path

from django.core.cache import cache
from django.utils.crypto import get_random_string

from .models import Server
from .ssh import exec_command, run_script

NRM_GROUP = "nrm_managed"
_CACHE_TIMEOUT = 1800
_FAILURE_CACHE_TIMEOUT = 30
_MANAGED_USERS_CACHE_KEY = "nrm:managed-users:{}"
_USER_GROUPS_CACHE_KEY = "nrm:user-groups:{}"

# 服务器管理脚本（代码库内，经 SFTP 上传目标机执行）
MGMT_SCRIPT = str(Path(__file__).parent / "scripts" / "nrm_mgmt.sh")

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
    "passwd",
    "mkdir",
    "tee",
)


def _run_mgmt(server, args, stdin_data=None, timeout=60):
    """上传并执行服务器管理脚本，返回 (ok, stdout, stderr)。"""
    return run_script(server, MGMT_SCRIPT, args, timeout=timeout, stdin_data=stdin_data)


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


def ensure_nrm_group(server):
    """确保目标机器存在 nrm_managed 组，不存在则创建。返回 (ok, msg)。"""
    ok, out, err = _run_mgmt(server, ["ensure_group", NRM_GROUP])
    if ok:
        return True, out or "nrm_managed 组已就绪"
    return False, err or "创建 nrm_managed 组失败"


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
    """将用户加入 nrm_managed 组（接管）。返回 (ok, msg)。

    接管同时剥离 root 级特权组（sudo/wheel/docker），
    只保留普通组 + nrm_managed——普通用户要普通，需要特权走正式申请。
    脚本内置容错：用户不存在时明确报错，不再由 usermod 报"用户不存在"。
    """
    username = (username or "").strip()
    if not username:
        return False, "用户名为空"
    ok, out, err = _run_mgmt(server, ["takeover", username])
    if ok:
        return True, out or f"用户 {username} 已加入 {NRM_GROUP} 组"
    return False, err or f"接管失败：{username}"


def get_managed_users_cached(server, force_refresh=False):
    """扫描目标机 nrm_managed 组成员并通过 Django cache 缓存（不落库）。

    返回 (members:list[str], msg)。force_refresh=True 时强制重新扫描。
    """
    key = _MANAGED_USERS_CACHE_KEY.format(server.pk)
    cached = None if force_refresh else cache.get(key)
    if cached is not None:
        return cached
    ok, members, msg = list_nrm_members(server)
    result = (members if ok else [], msg)
    cache.set(key, result, _CACHE_TIMEOUT if ok else _FAILURE_CACHE_TIMEOUT)
    return result


def clear_managed_users_cache(server):
    """清空指定服务器的受管用户缓存（刷新按钮调用）。"""
    cache.delete(_MANAGED_USERS_CACHE_KEY.format(server.pk))


def _set_user_locked(server, username, locked):
    """设置目标机器用户锁定状态，供启用/禁用公开操作复用。"""
    username = (username or "").strip()
    if not username:
        return False, "用户名为空"
    action, state = ("lock", "禁用") if locked else ("unlock", "启用")
    ok, _, err = _run_mgmt(server, [action, username])
    if ok:
        return True, f"用户 {username} 已{state}"
    return False, err or f"{state}失败：{username}"


def lock_user(server, username):
    """禁用目标机器用户（passwd -l）。返回 (ok, msg)。"""
    return _set_user_locked(server, username, True)


def unlock_user(server, username):
    """启用目标机器用户（passwd -u）。返回 (ok, msg)。"""
    return _set_user_locked(server, username, False)


def list_system_users(server):
    """列出目标机器可接管的系统用户（读取 /etc/passwd，uid≥1000 的真实登录用户）。

    返回 (ok, available_users:list, msg)。用于接管按钮化的候选列表。
    排除已受管成员（nrm_managed 组）与系统账号（uid<1000、nobody 65534）。
    """
    # 读取用户配置文件 /etc/passwd：取 uid 1000~65533 的真实登录用户
    # （仅扫描 /home 目录会漏掉 home 不在 /home 下的用户）
    cmd = r"awk -F: '$3>=1000 && $3<65534 {print $1}' /etc/passwd"
    ok, out, err = _exec(server, cmd)
    if not ok:
        return False, [], err or "读取用户列表失败"
    names = [n for n in out.split() if n]
    _, members, _ = list_nrm_members(server)
    available = [n for n in names if n not in members]
    return True, available, f"共 {len(available)} 个可接管用户"


def provision_user(server, username, groups=None, with_home=True, force_pwd_change=True):
    """在目标机器开通用户：建用户、加入分组、设置随机密码。

    groups: 要加入的机器分组列表（含 nrm_managed）。
    with_home: 是否预建 home 目录。申请了目录迁移时应为 False，
        让迁移流程把用户已有目录移到 /home/username。
    force_pwd_change: 是否强制首次登录修改密码（chage -d 0）。
    返回 (ok, password, msg)。

    全部操作收敛到 nrm_mgmt.sh provision 子命令执行：
    自动补建缺失组、useradd/usermod、chpasswd（stdin 传密码）、chage -d 0。
    """
    username = (username or "").strip()
    if not username:
        return False, "", "用户名为空"

    groups = list(groups or [])
    if NRM_GROUP not in groups:
        groups.append(NRM_GROUP)
    group_args = ",".join(groups)

    # 生成随机密码，经 stdin 传给脚本（避免出现在命令行参数里）
    password = get_random_string(16)
    args = [
        "provision",
        username,
        group_args,
        "1" if with_home else "0",
        "1" if force_pwd_change else "0",
    ]
    ok, out, err = _run_mgmt(server, args, stdin_data=f"{username}:{password}")

    if not ok:
        return False, "", err or f"开通失败：{username}"
    return True, password, out or f"用户 {username} 已开通（分组：{group_args}）"


def grant_sudo(server, username):
    """授予用户 root/sudo 权限（加入 sudo 组）。返回 (ok, group, msg)。

    收敛到脚本 grant_sudo 子命令：脚本自动探测 sudo/wheel 并加入。
    """
    username = (username or "").strip()
    if not username:
        return False, "", "用户名为空"
    ok, out, err = _run_mgmt(server, ["grant_sudo", username])
    if ok:
        match = re.search(r"\bgroup=(sudo|wheel)\b", out)
        if not match:
            return False, "", "目标机器未返回已授予的 sudo/wheel 组，结果无法确认"
        group = match.group(1)
        return True, group, f"用户 {username} 已加入 {group} 组，获得 sudo 权限"
    return False, "", err or "授予 sudo 失败"


def run_init_script(server):
    """在目标机执行仓库内置初始化脚本（经 SFTP 上传后以 root 运行），返回 (ok, msg)。

    初始化脚本独立维护于 servers/scripts/（与日常用户管理脚本 nrm_mgmt.sh 分工）。
    init_base.sh 负责基础准备（受管组 / motd 目录 / 工具链）。
    """
    from .scripts import INIT_BASE_SCRIPT  # 本函数内延迟导入避免循环

    ok, out, err = run_script(server, INIT_BASE_SCRIPT, timeout=120)
    if not ok:
        return False, f"基础初始化失败：{err or out[:200]}"
    return True, out or "基础初始化完成"


def usermod_add_group(server, username, group):
    """安全授予可申请的 root 级组；不创建伪 sudo/docker 组。"""
    username = (username or "").strip()
    group = (group or "").strip()
    if not username or not group:
        return False, "参数错误"
    if group == "sudo":
        ok, actual_group, msg = grant_sudo(server, username)
        if ok:
            return True, msg
        return False, msg or "授予 sudo 失败"
    if group != "docker":
        return False, f"不支持申请用户组：{group}"
    ok, out, err = _run_mgmt(server, ["grant_docker", username])
    if ok:
        return True, out or f"用户 {username} 已加入 {group} 组"
    return False, err or f"加入用户组失败：{group}"


def list_user_groups(server, usernames):
    """批量查询目标机用户所属组。返回 (ok, {username: [groups]}, msg)。

    一次 SSH 批量查询（脚本 list_groups 子命令），供详情页用户管理区展示。
    """
    names = [u for u in (usernames or []) if u]
    if not names:
        return True, {}, "无用户"
    ok, out, err = _run_mgmt(server, ["list_groups", ",".join(names)])
    if not ok:
        return False, {}, err or "查询用户组失败"
    result = {}
    for line in out.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) >= 2 and parts[0] == "USER_GROUPS":
            groups = [g for g in parts[2].split(",") if g] if len(parts) > 2 else []
            result[parts[1]] = groups
    return True, result, f"已查询 {len(result)} 个用户"


def sort_user_groups(username, groups):
    """用户组展示排序：排除用户本名组，nrm_managed 置顶、其他组按序。"""
    groups = [g for g in (groups or []) if g and g != username]
    priority = [g for g in groups if g == NRM_GROUP]
    others = sorted(g for g in groups if g != NRM_GROUP)
    return priority, others


def get_user_groups_cached(server, usernames, force_refresh=False):
    """读取目标机用户所属组（Django cache，未命中或强制刷新时批量 SSH 查询）。

    返回 {username: [groups]}；查询失败返回空 dict（详情页展示降级为空组）。
    """
    names = [u for u in (usernames or []) if u]
    key = _USER_GROUPS_CACHE_KEY.format(server.pk)
    cached = None if force_refresh else cache.get(key)
    if cached is not None and all(username in cached for username in names):
        return cached
    ok, groups_map, _ = list_user_groups(server, names)
    if ok:
        cache.set(key, groups_map, _CACHE_TIMEOUT)
        return groups_map
    return {}


def clear_user_groups_cache(server):
    """清空指定服务器的用户组缓存（增删组后或刷新时调用）。"""
    cache.delete(_USER_GROUPS_CACHE_KEY.format(server.pk))


def add_user_group(server, username, group):
    """将用户加入指定用户组（usermod -aG，组不存在自动创建）。返回 (ok, msg)。"""
    username = (username or "").strip()
    group = (group or "").strip()
    if not username or not group:
        return False, "参数错误"
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_\-]{0,31}", group):
        return False, f"非法组名：{group}"
    ok, out, err = _run_mgmt(server, ["add_group", username, group])
    if ok:
        return True, f"用户 {username} 已加入 {group} 组"
    return False, err or f"加入用户组失败：{group}"


def remove_user_group(server, username, group):
    """将用户从指定用户组移除（gpasswd -d）。返回 (ok, msg)。

    受管标识组 nrm_managed 禁止直接移除（用户是否受管由接管流程管理）。
    """
    username = (username or "").strip()
    group = (group or "").strip()
    if not username or not group:
        return False, "参数错误"
    if group in {NRM_GROUP, username}:
        return False, f"{group} 是受保护的用户组，不能直接移除"
    ok, out, err = _run_mgmt(server, ["del_group", username, group])
    if ok:
        return True, f"用户 {username} 已从 {group} 组移除"
    return False, err or f"移除用户组失败：{group}"


def _announcement_text():
    """启用公告拼成的终端文本（markdown 子集 → ANSI 彩色）。

    用于写入服务器 motd：SSH 登录时终端解释 ANSI 转义码显示颜色/高亮。
    """
    from accounts.markdown_convert import markdown_to_ansi
    from accounts.models import Announcement

    notices = [n for n in Announcement.objects.filter(enabled=True) if n.content.strip()]
    if not notices:
        return ""
    return "\n\n".join(markdown_to_ansi(n.content) for n in notices)


def write_server_motd(server, content=None):
    """把启用中的公告写入目标服务器 motd（/etc/motd.d/nrm_notifications），
    所有用户 SSH 登录时自动显示；无启用公告时清除 motd 文件。返回 (ok, msg)。
    """
    if content is None:
        content = _announcement_text()
    motd_file = "/etc/motd.d/nrm_notifications"
    # Ubuntu 使用 /etc/motd.d/ 聚合展示；确保目录存在后写入（root 权限）
    # 注意：不能用 `echo x > file`（重定向由当前 shell 执行，sudo 无法提权），
    # 走 `printf | tee`，再由 _sudo_wrap 仅为非 root SSH 用户的 tee 加 sudo。
    if content:
        ok, _, err = _exec(
            server,
            f"mkdir -p /etc/motd.d && printf '%s\\n' {shlex.quote(content)} | tee {motd_file} >/dev/null",
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
    content = _announcement_text()
    servers = [server] if server else list(Server.objects.all())
    done, fail = 0, []
    for s in servers:
        ok, msg = write_server_motd(s, content)
        if ok:
            done += 1
        else:
            fail.append(f"{s.name}：{msg}")
    return not fail, f"公告推送完成：{done} 台服务器 motd" + (f"；失败：{'；'.join(fail)}" if fail else "")
