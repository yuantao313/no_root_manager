#!/usr/bin/env bash
# NRM NPU 卡信息采集脚本：仅 NPU 服务器，root 执行。
# 由 NRM 平台经 SFTP 上传后以 root 一次性执行（servers/devices.py）。
#
# 职责：在目标机执行 `npu-smi info`，把原始表格文本原样输出，交给平台侧
# Python 解析（servers/npu_smi.py：NPU ID / 型号 / Health / 功耗 / 温度 /
# 利用率 / HBM 内存；detect_npu_groups 卡组检测也复用该输出）。
# 不再依赖 python + acl 逐卡查询，也不再扫描 /dev/davinciN。
#
# 输出约定（平台侧解析）：
#   npu-smi info 原始表格文本（stdout 直通）
#   NPU_ERROR=<说明>   （可选，npu-smi 不可用 / 无输出时降级输出）
set -euo pipefail

log() { echo "[npu_info] $*" >&2; }

# ---------- 定位 npu-smi 可执行文件 ----------
# 非登录 SSH shell 的 PATH 可能不含 CANN 目录，按常见安装位置逐个兜底。
NPU_SMI=""
if command -v npu-smi >/dev/null 2>&1; then
    NPU_SMI="$(command -v npu-smi)"
else
    for cand in /usr/local/Ascend/driver/tools/npu-smi \
                /usr/local/Ascend/toolbox/tools/npu-smi \
                /usr/local/Ascend/ascend-toolkit/tools/npu-smi; do
        if [ -x "$cand" ]; then
            NPU_SMI="$cand"
            break
        fi
    done
fi
if [ -z "$NPU_SMI" ]; then
    echo "NPU_ERROR=未找到 npu-smi（Ascend 驱动未安装或不在 PATH）"
    exit 0
fi

# ---------- 执行 npu-smi info（timeout 60 + 重试一次，防驱动响应慢卡死）----------
# npu-smi 读取设备/HBM 内存需要足够权限：root 直接执行；
# 非 root（外层 sudo -n bash 提权不可用的场景）显式 sudo -n 提权。
run_smi() {
    if [ "$(id -u)" = "0" ]; then
        timeout 60 "$NPU_SMI" info 2>/dev/null
    else
        timeout 60 sudo -n "$NPU_SMI" info 2>/dev/null
    fi
}

out=""
for attempt in 1 2; do
    # set -euo pipefail 下命令替换必须 || true：超时返回 124 时不退出脚本，
    # 而是走下方 NPU_ERROR 降级输出，避免整个采集卡死
    out=$(run_smi) || true
    if [ -n "$out" ]; then
        break
    fi
    log "npu-smi info 第 $attempt 次无输出，重试"
done
if [ -z "$out" ]; then
    echo "NPU_ERROR=npu-smi info 无输出（驱动未加载或响应超时），请稍后重试"
    exit 0
fi
echo "$out"
