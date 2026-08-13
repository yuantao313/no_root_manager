#!/usr/bin/env bash
# NRM Ascend NPU 初始化脚本：仅 NPU 服务器执行（幂等，可重复运行）。
# 由 NRM 平台经 SFTP 上传到目标机后以 root 执行（run_init_script）。
#
# 职责（与平台侧 detect_npu_groups / npu_info.sh 分工）：
#   1. 检测 /dev/davinciN 确认 Ascend 驱动已加载
#   2. 创建公共组 npu 与每卡组 npuN（用户申请卡组时 usermod -aG 即获访问权）
#   3. 写 udev 规则把设备节点归属到对应组，实现"不同用户组隔离不同 NPU"：
#        - /dev/davinciN   → npuN 组（只有该卡组用户可访问）
#        - 公共管理设备     → npu 组（davinci_manager/svm 等，所有 NPU 用户可访问）
#   4. reload + trigger udev，并 chgrp/chmod 兜底当前已存在的设备节点
#      （udev 触发对已存在节点不一定即时生效，直接改归属保证本次立即隔离）
# 未来若支持 GPU：另建 init_gpu.sh 独立维护，本脚本不掺 GPU 逻辑。
set -euo pipefail

log() { echo "[init_ascend_npu] $*" >&2; }

RULE_FILE="/etc/udev/rules.d/99-nrm-npu.rules"

ensure_group() {
    local g="$1"
    if ! getent group "$g" >/dev/null 2>&1; then
        groupadd "$g" || { log "创建组 $g 失败"; exit 3; }
        log "创建组 $g"
    else
        log "组 $g 已存在"
    fi
}

# 1. 检测 NPU 卡（Ascend 驱动未加载时无 /dev/davinciN）
devs=$(ls -1 /dev/davinci[0-9]* 2>/dev/null || true)
if [ -z "$devs" ]; then
    log "未检测到 /dev/davinciN（Ascend 驱动未加载或未安装）"
    exit 2
fi

# 2. 公共组 + 每卡组
ensure_group npu
for dev in $devs; do
    idx="${dev##*davinci}"
    ensure_group "npu${idx}"
done

# 3. 写 udev 规则：每张卡归属 npuN 组（卡组隔离），公共管理设备归属 npu 组
{
    echo "# NRM NPU 卡组隔离（由 init_ascend_npu.sh 生成，可重复运行覆盖）"
    for dev in $devs; do
        idx="${dev##*davinci}"
        echo "KERNEL==\"davinci${idx}\", OWNER=\"root\", GROUP=\"npu${idx}\", MODE=\"0660\""
    done
    # 公共管理设备（davinci_manager / davinci_svm / davinci_hdc 等非卡节点）归 npu 组
    echo "KERNEL==\"davinci_manager\", OWNER=\"root\", GROUP=\"npu\", MODE=\"0660\""
    echo "KERNEL==\"davinci_svm\", OWNER=\"root\", GROUP=\"npu\", MODE=\"0660\""
    echo "KERNEL==\"davinci_hdc\", OWNER=\"root\", GROUP=\"npu\", MODE=\"0660\""
    # 兜底：未列出的 davinci 节点也至少归公共组（权限并集，不影响卡组隔离）
    echo "KERNEL==\"davinci*\", OWNER=\"root\", GROUP=\"npu\", MODE=\"0660\""
} > "$RULE_FILE"
log "已写入 $RULE_FILE"

# 4. reload + trigger udev；并对已存在节点 chgrp/chmod 兜底（保证本次立即生效）
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules 2>/dev/null || log "udev reload 失败（跳过）"
    udevadm trigger --subsystem-match=char --action=change 2>/dev/null || true
fi
for dev in $devs; do
    idx="${dev##*davinci}"
    chgrp "npu${idx}" "$dev" 2>/dev/null || log "chgrp $dev 失败（跳过）"
    chmod 660 "$dev" 2>/dev/null || true
done
for mgmt in /dev/davinci_manager /dev/davinci_svm /dev/davinci_hdc; do
    [ -e "$mgmt" ] && { chgrp npu "$mgmt" 2>/dev/null || true; chmod 660 "$mgmt" 2>/dev/null || true; }
done

echo "OK init_ascend_npu"
