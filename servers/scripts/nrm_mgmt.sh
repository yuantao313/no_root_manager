#!/usr/bin/env bash
# NRM 目标机用户管理脚本（由 NRM 平台经 SFTP 上传到目标机后以 root 执行）。
#
# 设计原则：
# - 所有常用服务器操作（建用户/接管/锁定/解锁/sudo/NPU 授权）收敛到本脚本，
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
    local -a group_items
    IFS=',' read -ra group_items <<< "$list"
    for g in "${group_items[@]}"; do
        [ -n "$g" ] && ensure_group "$g"
    done
}

# 判断用户当前是否属于指定组（同时覆盖主组和附加组）。
user_in_group() {
    local username="$1" expected="$2" current
    for current in $(id -nG "$username"); do
        [ "$current" = "$expected" ] && return 0
    done
    return 1
}

# 幂等移除组成员关系：本来不是成员视为成功，真实删除失败或删除后仍残留则失败。
remove_group_membership() {
    local username="$1" group="$2"
    getent group "$group" >/dev/null 2>&1 || return 0
    user_in_group "$username" "$group" || return 0
    if ! gpasswd -d "$username" "$group" >/dev/null 2>&1; then
        log "从组 $group 移除用户 $username 失败"
        return 1
    fi
    if user_in_group "$username" "$group"; then
        log "用户 $username 仍属于组 ${group}，拒绝报告成功"
        return 1
    fi
}

rollback_provision_user() {
    local username="$1"
    # 若删除账号失败，至少尝试锁定，避免半配置账号继续使用。
    passwd -l "$username" >/dev/null 2>&1 || true
    if ! userdel "$username" >/dev/null 2>&1; then
        log "回滚新账号 $username 失败；账号已尝试锁定，请人工核查"
        return 1
    fi
}

case "${1:-}" in
    provision)
        # provision <username> <groups_csv> [with_home=1|0] [force_pwd_change=1|0]
        # 密码经 stdin 第一行传入：user:password
        username="$2"; groups_csv="$3"; with_home="${4:-1}"; force_pwd="${5:-1}"
        [ -n "$username" ] && [ -n "$groups_csv" ] || { log "参数错误"; exit 2; }
        # 在创建组或账号前先完整取得凭据，避免 stdin 中断留下半成品账号。
        read -r userpass || { log "未收到密码"; exit 4; }
        case "$userpass" in
            "$username":*) ;;
            *) log "密码输入的用户名与目标用户不一致"; exit 4 ;;
        esac
        # provision 只用于创建新账号。同名用户必须走 takeover，防止误重置现有密码。
        if id -u "$username" >/dev/null 2>&1; then
            log "目标机器用户 $username 已存在，拒绝开通；请使用转移接管流程"
            exit 5
        fi
        ensure_groups "$groups_csv"
        home_flag=""
        [ "$with_home" = "1" ] && home_flag="-m"
        useradd $home_flag -s /bin/bash -G "$groups_csv" "$username"
        if ! printf '%s\n' "$userpass" | chpasswd; then
            rollback_provision_user "$username" || true
            log "设置新账号密码失败，已尝试回滚"
            exit 6
        fi
        if [ "$force_pwd" = "1" ] && ! chage -d 0 "$username"; then
            rollback_provision_user "$username" || true
            log "设置首次改密失败，已尝试回滚"
            exit 6
        fi
        echo "OK provision $username groups=$groups_csv"
        ;;

    takeover)
        # takeover <username>：接管为受管用户（加入 nrm_managed 组）。
        # 同时剥离特权/驱动专用组（HwHiAiUser/sudo/wheel/npu 卡组），只保留
        # 普通组 + nrm_managed——"普通用户要普通"，需要特权走正式申请流程。
        username="$2"
        [ -n "$username" ] || { log "用户名为空"; exit 2; }
        require_user "$username"
        ensure_group "$NRM_GROUP"
        # 从固定特权/驱动组剥离；任何真实删除失败都阻止接管成功。
        for g in HwHiAiUser sudo wheel docker; do
            remove_group_membership "$username" "$g" || exit 6
        done
        # 剥离所有 NPU 卡组（npu、npuN），避免接管后残留算力访问权
        for g in $(getent group 2>/dev/null | awk -F: '$1 ~ /^npu/ {print $1}'); do
            remove_group_membership "$username" "$g" || exit 6
        done
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
        # grant_sudo <username>：自动探测并加入已有 sudo/wheel 组
        username="$2"; group=""
        require_user "$username"
        for g in sudo wheel; do
            if getent group "$g" >/dev/null 2>&1; then group="$g"; break; fi
        done
        if [ -z "$group" ]; then
            log "未找到 sudo/wheel 组，无法授予 sudo 权限"
            exit 3
        fi
        usermod -aG "$group" "$username"
        user_in_group "$username" "$group" || { log "用户未实际加入 $group 组"; exit 6; }
        echo "OK grant_sudo $username group=${group:-none}"
        ;;

    grant_docker)
        # grant_docker <username>：只使用现有 Docker 安装创建的 docker 组，绝不临时造组。
        username="$2"
        require_user "$username"
        getent group docker >/dev/null 2>&1 || { log "目标机器不存在 docker 组"; exit 3; }
        command -v docker >/dev/null 2>&1 || { log "目标机器未安装 docker 命令"; exit 3; }
        usermod -aG docker "$username"
        user_in_group "$username" docker || { log "用户未实际加入 docker 组"; exit 6; }
        echo "OK grant_docker $username group=docker"
        ;;

    revoke_sudo)
        # revoke_sudo <username>：从所有 sudo 组移除
        username="$2"
        require_user "$username"
        for g in sudo wheel; do
            remove_group_membership "$username" "$g" || exit 6
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

    list_groups)
        # list_groups <username_csv>：批量输出用户所属组（一次 SSH 完成，详情页展示用）
        # 输出约定：USER_GROUPS <username> <组1,组2,...>（每用户一行；不存在的用户标注）
        users_csv="$2"
        [ -n "$users_csv" ] || { log "参数错误"; exit 2; }
        IFS=',' read -ra users <<< "$users_csv"
        for u in "${users[@]}"; do
            [ -n "$u" ] || continue
            if id -u "$u" >/dev/null 2>&1; then
                user_groups=$(id -nG "$u" | tr ' ' ',')
                echo "USER_GROUPS $u ${user_groups:-无}"
            else
                echo "USER_GROUPS $u (用户不存在)"
            fi
        done
        ;;

    add_group)
        # add_group <username> <group>：将用户加入指定用户组（组不存在自动创建）
        username="$2"; group="$3"
        [ -n "$username" ] && [ -n "$group" ] || { log "参数错误"; exit 2; }
        require_user "$username"
        ensure_group "$group"
        usermod -aG "$group" "$username"
        echo "OK add_group $username group=$group"
        ;;

    del_group)
        # del_group <username> <group>：将用户从指定用户组移除（组不存在时忽略）
        username="$2"; group="$3"
        [ -n "$username" ] && [ -n "$group" ] || { log "参数错误"; exit 2; }
        require_user "$username"
        remove_group_membership "$username" "$group" || exit 6
        echo "OK del_group $username group=$group"
        ;;

    ensure_group)
        # ensure_group <group>
        ensure_group "$2"
        echo "OK ensure_group $2"
        ;;

    *)
        log "用法: nrm_mgmt.sh <provision|takeover|lock|unlock|grant_sudo|grant_docker|revoke_sudo|grant_npu|ensure_group|list_groups|add_group|del_group> [参数...]"
        exit 1
        ;;
esac
