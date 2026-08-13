#!/usr/bin/env bash
# NRM NPU 卡信息采集脚本：Ascend 设备型号与内存（仅 NPU 服务器，root 执行）。
# 由 NRM 平台经 SFTP 上传后以 root 一次性执行（servers/devices.py）。
#
# 独立于主机信息（host_info.sh）与 GPU（gpu_info.sh），各设备类型互不干扰。
# 分工：shell 负责扫描设备列表（ls /dev/davinci*），python 单进程循环查询
# 所有卡的型号/内存（型号 acl.get_soc_name()，内存 acl.rt.get_mem_info(dev)[1] 字节→GB），
# 避免每张卡起一个 python3（启动 + import acl 开销大）。
#
# 输出约定（NPU 命名空间，Python 侧解析）：
#   NPU_CARD <设备号> <内存GB> <型号…>   （每卡一行；型号可能含空格，故放最后）
#   NPU_ERROR=<说明>                       （可选，探测失败时）
set -euo pipefail

log() { echo "[npu_info] $*" >&2; }

# ---------- 探测可用的 python（含 acl）----------
# CANN 环境脚本路径不固定（实测存在 cann / cann-9.1.0 / ascend-toolkit / toolbox 等），
# 按常见安装位置逐个尝试，找到第一个可用即加载；都缺失则报错。
CANN_ENV=""
for env in /usr/local/Ascend/cann/set_env.sh \
           /usr/local/Ascend/cann-*/set_env.sh \
           /usr/local/Ascend/ascend-toolkit/set_env.sh \
           /usr/local/Ascend/toolbox/set_env.sh; do
    if [ -f "$env" ]; then
        CANN_ENV="$env"
        # shellcheck disable=SC1090
        # shellcheck disable=SC1091
        source "$env" >/dev/null 2>&1 || true
        break
    fi
done

py=""
if command -v python3 >/dev/null 2>&1 && python3 -c "import acl" >/dev/null 2>&1; then
    py="python3"
elif command -v python >/dev/null 2>&1 && python -c "import acl" >/dev/null 2>&1; then
    py="python"
fi

if [ -z "$py" ]; then
    echo "NPU_ERROR=未找到含 acl 库的 python（已尝试加载 $CANN_ENV）；请确认 Ascend 驱动与 CANN 已安装"
    exit 0
fi

# ---------- shell 扫描设备列表 ----------
devs=$(ls -1 /dev/davinci[0-9]* 2>/dev/null || true)
if [ -z "$devs" ]; then
    echo "NPU_ERROR=未检测到 /dev/davinciN（Ascend 驱动未加载）"
    exit 0
fi
# 提取设备号（如 davinci0 → 0），空格分隔传给 python
idxs=""
for dev in $devs; do
    idx="${dev##*davinci}"
    idxs="$idxs $idx"
done

# ---------- python 单进程循环查询所有卡（设备号经环境变量传入）----------
NPU_IDXS="$idxs" "$py" - <<'PYEOF'
import os

import acl

indices = [int(i) for i in os.environ.get("NPU_IDXS", "").split() if i.isdigit()]
for idx in indices:
    try:
        _ = acl.rt.set_device(idx)
        soc = acl.get_soc_name()
        _1, total, _2 = acl.rt.get_mem_info(0)
        mem_gb = int(int(total) / 1024**3) if total else 0
    except Exception:  # noqa: BLE001 —— 单卡失败不影响其他卡
        print(f"NPU_CARD {idx} 0 未知")
        continue
    print(f"NPU_CARD {idx} {mem_gb} {soc or '未知'}")
PYEOF
