#!/usr/bin/env bash
# NRM 目标机用户管理脚本（由 NRM 平台经 SFTP 上传到目标机后以 root 执行）。
#
# 设计原则：
# - 所有常用服务器操作（建用户/接管/锁定/解锁/sudo/NPU 授权/资源限制）收敛到本脚本，
#   不散落在 Python 代码的字符串命令里
# - 子命令模式：nrm_mgmt.sh <子命令> [参数...]
# - 每个子命令成功输出 "OK <子命令> ..."，失败以非零退出码 + stderr 提示
# - 容错：用户不存在时明确报错退出；组不存在时自动补建
set -euo pipefail

NRM_GROUP="nrm_managed"

log() { echo "[nrm] $*" >&2; }

# 确保用户存在（不存在则明确报错退出，避免 usermod 报"用户不存在"）
require_user() {
    local u="$1"
    if ! id -u "$u" >/dev/null 2>&1; then
        log "目标机器用户 $u 不存在"
        exit 2
    fi
}

# 确保组存在（不存在则创建）
ensure_group() {
    local g="$1"
    if ! getent group "$g" >/dev/null 2>&1; then
        groupadd "$g" || { log "创建组 $g 失败"; exit 3; }
    fi
}

# 逐个确保组存在（逗号分隔列表）
ensure_groups() {
    local list="$1" g
    IFS=',' read -ra groups <<< "$list"
    for g in "${groups[@]}"; do
        [ -n "$g" ] && ensure_group "$g"
    done
}

case "${1:-}" in
    provision)
        # provision <username> <groups_csv> [expire_date|-] [with_home=1|0] [force_pwd_change=1|0]
        # 密码经 stdin 第一行传入：user:password
        username="$2"; groups_csv="$3"; expire="${4:--}"; with_home="${5:-1}"; force_pwd="${6:-1}"
        [ -n "$username" ] || { log "用户名为空"; exit 2; }
        ensure_groups "$groups_csv"
        if id -u "$username" >/dev/null 2>&1; then
            usermod -aG "$groups_csv" "$username"
        else
            home_flag=""
            [ "$with_home" = "1" ] && home_flag="-m"
            useradd $home_flag -s /bin/bash -G "$groups_csv" "$username"
        fi
        [ "$expire" != "-" ] && usermod -e "$expire" "$username"
        read -r userpass || { log "未收到密码"; exit 4; }
        echo "$userpass" | chpasswd
        [ "$force_pwd" = "1" ] && chage -d 0 "$username"
        echo "OK provision $username groups=$groups_csv"
        ;;

    takeover)
        # takeover <username>：加入 nrm_managed 组（接管为受管用户）
        username="$2"
        [ -n "$username" ] || { log "用户名为空"; exit 2; }
        require_user "$username"
        ensure_group "$NRM_GROUP"
        usermod -aG "$NRM_GROUP" "$username"
        echo "OK takeover $username"
        ;;

    lock)
        # lock <username>：禁用用户
        username="$2"
        require_user "$username"
        passwd -l "$username" >/dev/null
        echo "OK lock $username"
        ;;

    unlock)
        # unlock <username>：启用用户
        username="$2"
        require_user "$username"
        passwd -u "$username" >/dev/null
        echo "OK unlock $username"
        ;;

    grant_sudo)
        # grant_sudo <username> [group]：加入 sudo/wheel 组
        username="$2"; group="${3:-}"
        require_user "$username"
        if [ -z "$group" ]; then
            for g in sudo wheel; do
                if getent group "$g" >/dev/null 2>&1; then group="$g"; break; fi
            done
        fi
        if [ -n "$group" ]; then
            ensure_group "$group"
            usermod -aG "$group" "$username"
        fi
        echo "OK grant_sudo $username group=${group:-none}"
        ;;

    revoke_sudo)
        # revoke_sudo <username>：从所有 sudo 组移除
        username="$2"
        require_user "$username"
        for g in sudo wheel; do
            if getent group "$g" >/dev/null 2>&1; then
                gpasswd -d "$username" "$g" >/dev/null 2>&1 || true
            fi
        done
        echo "OK revoke_sudo $username"
        ;;

    grant_npu)
        # grant_npu <username> <groups_csv>：授权 NPU 卡组（含公共组 npu）
        username="$2"; groups_csv="$3"
        [ -n "$username" ] && [ -n "$groups_csv" ] || { log "参数错误"; exit 2; }
        require_user "$username"
        ensure_groups "$groups_csv"
        usermod -aG "$groups_csv" "$username"
        echo "OK grant_npu $username groups=$groups_csv"
        ;;

    set_limits)
        # set_limits <username> <item1=val1> <item2=val2> ...：写入 limits.d 独立文件
        username="$2"; shift 2
        [ -n "$username" ] || { log "用户名为空"; exit 2; }
        conf="/etc/security/limits.d/nrm-$username.conf"
        : > "$conf"
        for kv in "$@"; do
            item="${kv%%=*}"
            val="${kv#*=}"
            echo "$username hard $item $val" >> "$conf"
        done
        echo "OK set_limits $username -> $conf"
        ;;

    ensure_group)
        # ensure_group <group>
        ensure_group "$2"
        echo "OK ensure_group $2"
        ;;

    *)
        log "用法: nrm_mgmt.sh <provision|takeover|lock|unlock|grant_sudo|revoke_sudo|grant_npu|set_limits|ensure_group> [参数...]"
        exit 1
        ;;
esac
