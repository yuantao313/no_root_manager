"""目标机器用户接管与开通服务：所有受管用户加入 nrm_managed 组。

常用服务器操作（建用户/接管/锁定/解锁/sudo/NPU 授权/资源限制）统一收敛到
servers/scripts/nrm_mgmt.sh 脚本，经 SFTP 上传目标机后以 root 执行，
不再在 Python 中散落字符串命令。
"""

import logging
import re
import secrets
import shlex
import string
from pathlib import Path

from .models import Server
from .ssh import exec_command, run_script

logger = logging.getLogger(__name__)

NRM_GROUP = "nrm_managed"

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


def _random_password(length=16):
    """生成随机密码（字母+数字，排除易混淆字符）。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


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

    接管同时剥离特权/驱动专用组（HwHiAiUser/sudo/wheel/npu 卡组），
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


# 受管用户内存缓存（进程内，避免每次访问都 SSH 扫描；刷新按钮清缓存）
_MANAGED_USERS_CACHE: dict[int, tuple[list, str]] = {}

# NPU 状态内存缓存：server_id -> {"is_npu": bool, "groups": list, "msg": str}
# 添加服务器时与服务启动时同步，申请界面读取走缓存，避免每次 SSH 检测卡顿
_NPU_STATE_CACHE: dict[int, dict] = {}


def get_npu_state_cached(server, force_refresh=False):
    """读取服务器 NPU 状态（内存缓存，未命中或强制刷新时才 SSH 检测）。

    返回 {"is_npu": bool, "groups": list[str], "msg": str}。
    非 NPU 服务器直接返回空缓存（不 SSH）；NPU 服务器首次/刷新时
    调用 detect_npu_groups 检测并缓存，检测失败时降级用库内 npu_groups。
    """
    key = server.pk
    if not force_refresh and key in _NPU_STATE_CACHE:
        return _NPU_STATE_CACHE[key]
    if not server.is_npu:
        state = {"is_npu": False, "groups": [], "msg": ""}
        _NPU_STATE_CACHE[key] = state
        return state
    ok, groups, msg = detect_npu_groups(server)
    state = {
        "is_npu": ok,
        "groups": groups if ok else server.npu_groups_list(),
        "msg": msg if ok else (msg or "NPU 检测失败，使用已保存配置"),
    }
    _NPU_STATE_CACHE[key] = state
    return state


def sync_npu_states():
    """同步全部 NPU 服务器的状态到内存缓存（服务启动时调用，后台线程执行）。"""
    from .models import Server

    for server in Server.objects.filter(is_npu=True).only("pk", "is_npu", "npu_groups"):
        try:
            get_npu_state_cached(server, force_refresh=True)
        except Exception:  # noqa: BLE001 —— 单台失败不影响其余
            logger.exception("NPU 状态同步失败：%s", server)


def clear_npu_state_cache(server=None):
    """清空 NPU 状态内存缓存（服务器修改后调用）。"""
    if server is None:
        _NPU_STATE_CACHE.clear()
    else:
        _NPU_STATE_CACHE.pop(server.pk, None)


def get_managed_users_cached(server, force_refresh=False):
    """扫描目标机 nrm_managed 组成员并缓存到内存（不落库）。

    返回 (members:list[str], msg)。force_refresh=True 时强制重新扫描。
    """
    key = server.pk
    if not force_refresh and key in _MANAGED_USERS_CACHE:
        return _MANAGED_USERS_CACHE[key]
    ok, members, msg = list_nrm_members(server)
    if not ok:
        _MANAGED_USERS_CACHE[key] = ([], msg)
        return [], msg
    _MANAGED_USERS_CACHE[key] = (members, msg)
    return members, msg


def clear_managed_users_cache(server=None):
    """清空受管用户内存缓存（刷新按钮调用）。"""
    if server is None:
        _MANAGED_USERS_CACHE.clear()
    else:
        _MANAGED_USERS_CACHE.pop(server.pk, None)


def sync_managed_users(server):
    """兼容入口：不再写数据库，改为返回内存缓存扫描结果。返回 (ok, msg)。"""
    members, msg = get_managed_users_cached(server, force_refresh=True)
    return True, f"已扫描 {len(members)} 个受管用户：{msg}"


def lock_user(server, username):
    """禁用目标机器用户（passwd -l）。返回 (ok, msg)。"""
    username = (username or "").strip()
    if not username:
        return False, "用户名为空"
    ok, out, err = _run_mgmt(server, ["lock", username])
    if ok:
        return True, f"用户 {username} 已禁用"
    return False, err or f"禁用失败：{username}"


def unlock_user(server, username):
    """启用目标机器用户（passwd -u）。返回 (ok, msg)。"""
    username = (username or "").strip()
    if not username:
        return False, "用户名为空"
    ok, out, err = _run_mgmt(server, ["unlock", username])
    if ok:
        return True, f"用户 {username} 已启用"
    return False, err or f"启用失败：{username}"


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
    """兼容入口：采集受管用户资源使用（走内存缓存，不写数据库）。返回 (ok, msg)。

    各用户资源使用直接采集并随缓存返回；如需持久化展示可自行调用 collect_user_usage。
    """
    members, _ = get_managed_users_cached(server, force_refresh=True)
    if not members:
        return True, "无受管用户可采集"
    collected = 0
    for name in members:
        ok, data, _ = collect_user_usage(server, name)
        if ok and data.get("disk"):
            collected += 1
    return True, f"资源采集完成：{collected}/{len(members)} 个用户"


def provision_user(server, username, groups=None, with_home=True, force_pwd_change=True):
    """在目标机器开通用户：建用户、加入分组、设置随机密码、写入资源限制。

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
    password = _random_password()
    args = [
        "provision",
        username,
        group_args,
        "1" if with_home else "0",
        "1" if force_pwd_change else "0",
    ]
    ok, out, err = _run_mgmt(server, args, stdin_data=f"{username}:{password}")

    # 写入资源限制（ulimit），防止单个用户耗尽服务器资源
    if ok and any(
        [
            server.nproc_limit,
            server.nofile_limit,
            server.as_limit,
            server.core_limit,
            server.fsize_limit,
            server.maxlogins_limit,
        ]
    ):
        ok, limit_err = apply_resource_limits(server, username)
        if not ok:
            return False, "", f"设置资源限制失败：{limit_err}"

    if not ok:
        return False, "", err or f"开通失败：{username}"
    return True, password, out or f"用户 {username} 已开通（分组：{group_args}）"


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

    items = []
    for field, item in RESOURCE_LIMIT_ITEMS:
        value = getattr(server, field, 0)
        if value:
            items.append(f"{item}={value}")
    if not items:
        return True, "未配置资源限制"

    # 收敛到脚本 set_limits 子命令：脚本内写 /etc/security/limits.d/nrm-<user>.conf
    ok, out, err = _run_mgmt(server, ["set_limits", username] + items)
    if ok:
        return True, out or f"已写入资源限制：{', '.join(items)}"
    return False, err or "写入失败"


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
    """授予用户 root/sudo 权限（加入 sudo 组）。返回 (ok, group, msg)。

    收敛到脚本 grant_sudo 子命令：脚本自动探测 sudo/wheel 并加入。
    """
    username = (username or "").strip()
    if not username:
        return False, "", "用户名为空"
    ok, out, err = _run_mgmt(server, ["grant_sudo", username])
    if ok:
        return True, "sudo", out or f"用户 {username} 已加入 sudo 组，获得 sudo 权限"
    return False, "", err or "授予 sudo 失败"


def revoke_sudo(server, username):
    """撤销用户 sudo 权限（从 sudo/wheel 组移除）。返回 (ok, msg)。

    收敛到脚本 revoke_sudo 子命令：脚本从所有可能的 sudo 组移除。
    """
    username = (username or "").strip()
    if not username:
        return False, "用户名为空"
    ok, out, err = _run_mgmt(server, ["revoke_sudo", username])
    if ok:
        return True, out or f"已撤销用户 {username} 的 sudo 权限"
    return False, err or "撤销 sudo 失败"


def run_init_script(server):
    """在目标机执行仓库内置初始化脚本（经 SFTP 上传后以 root 运行），返回 (ok, msg)。

    初始化脚本独立维护于 servers/scripts/（与日常用户管理脚本 nrm_mgmt.sh 分工）：
      - init_base.sh：基础准备（受管组 / motd 目录 / 工具链），所有服务器执行
      - init_ascend_npu.sh：NPU 初始化（检测 davinci 卡并建卡组），仅 NPU 服务器执行
    未来支持 GPU 时另建 init_gpu.sh，此处按服务器类型组合执行。
    """
    from .scripts import INIT_BASE_SCRIPT, INIT_NPU_SCRIPT  # 本函数内延迟导入避免循环

    results = []
    ok, out, err = run_script(server, INIT_BASE_SCRIPT, timeout=120)
    if not ok:
        return False, f"基础初始化失败：{err or out[:200]}"
    results.append(out or "基础初始化完成")
    if server.is_npu:
        ok, out, err = run_script(server, INIT_NPU_SCRIPT, timeout=120)
        if not ok:
            return False, f"NPU 初始化失败：{err or out[:200]}"
        results.append(out or "NPU 初始化完成")
    return True, "；".join(results)


def detect_npu_groups(server):
    """检测目标机 NPU 卡组：返回 (ok, groups_list, msg)，groups 含公共组 npu + 卡组 npuN。

    复用 npu_info.sh（目标机跑 ``npu-smi info`` 原样带回）解析 NPU ID，
    与设备信息采集共用同一数据源，避免 ls /dev/davinciN 与 npu-smi 结果不一致。
    """
    from .npu_smi import parse_npu_smi_info
    from .scripts import NPU_INFO_SCRIPT

    ok, out, err = run_script(server, NPU_INFO_SCRIPT, timeout=150, connect_timeout=5)
    if not ok:
        return False, [], err or "NPU 检测失败（npu-smi info 执行失败）"
    cards, npu_err = parse_npu_smi_info(out)
    if npu_err:
        return False, [], npu_err
    if not cards:
        return False, [], "未检测到 NPU 卡（npu-smi info 无设备输出）"
    ids = sorted({c["index"] for c in cards})
    groups = ["npu"] + [f"npu{i}" for i in ids]
    return True, groups, f"检测到 {len(ids)} 张 NPU 卡：{ids}"


def grant_npu_access(server, username, groups):
    """授权用户 NPU 卡组（usermod -aG npu,npuN）。返回 (ok, msg)。

    收敛到脚本 grant_npu 子命令：脚本内确保组存在并授权，
    用户不存在时明确报错（容错，不再由 usermod 报"用户不存在"）。
    """
    username = (username or "").strip()
    if not username or not groups:
        return False, "参数错误"
    group_args = ",".join(groups)
    ok, out, err = _run_mgmt(server, ["grant_npu", username, group_args])
    if ok:
        return True, out or f"用户 {username} 已加入 NPU 卡组：{group_args}"
    return False, err or f"NPU 授权失败：{username}"


def usermod_add_group(server, username, group):
    """将用户加入单个用户组（usermod -aG <group>，如 sudo/docker）。返回 (ok, msg)。

    收敛到脚本 grant_sudo 子命令（其支持指定组名），
    用户不存在时由脚本明确报错。
    """
    username = (username or "").strip()
    group = (group or "").strip()
    if not username or not group:
        return False, "参数错误"
    ok, out, err = _run_mgmt(server, ["grant_sudo", username, group])
    if ok:
        return True, out or f"用户 {username} 已加入 {group} 组"
    return False, err or f"加入用户组失败：{group}"


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


def write_server_motd(server):
    """把启用中的公告写入目标服务器 motd（/etc/motd.d/nrm_notifications），
    所有用户 SSH 登录时自动显示；无启用公告时清除 motd 文件。返回 (ok, msg)。
    """
    content = _announcement_text()
    motd_file = "/etc/motd.d/nrm_notifications"
    # Ubuntu 使用 /etc/motd.d/ 聚合展示；确保目录存在后写入（root 权限）
    # 注意：不能用 `echo x > file`（重定向由当前 shell 执行，sudo 无法提权），
    # 必须走 `printf | sudo -n tee` 让 tee 以 root 写文件（与资源限制写入一致）
    if content:
        ok, _, err = _exec(
            server,
            f"mkdir -p /etc/motd.d && printf '%s\\n' {shlex.quote(content)} | sudo -n tee {motd_file} >/dev/null",
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
