#!/usr/bin/env bash
# NRM 主机信息采集脚本：CPU / 内存 / 硬盘（所有服务器通用，root 执行）。
# 由 NRM 平台经 SFTP 上传后以 root 一次性执行（servers/devices.py）。
#
# 与设备卡采集分离：本脚本只查主机基础信息；NPU 卡见 npu_info.sh，
# GPU 卡见 gpu_info.sh（预留）——各设备类型独立维护、互不干扰。
#
# 输出约定（key=value，Python 侧解析）：
#   CPU_VENDOR=<厂商>         （lscpu Vendor ID，如 GenuineIntel/AMD/HiSilicon）
#   CPU_MODEL=<型号>          （lscpu 优先，兼容鲲鹏 ARM 无 model name）
#   CPU_FREQ_MHZ=<频率 MHz>   （优先 lscpu max MHz，兜底当前 MHz）
#   CPU_CORES=<核数>
#   MEM_TOTAL=<如 512G>       （/proc/meminfo MemTotal）
#   DISK_ROOT=<如 916G（已用 120G）>
set -euo pipefail

# CPU 厂商/型号/频率：lscpu 优先，兼容中英文 locale 与中英文冒号（-F'[:：]'）。
# 注意 set -euo pipefail：grep 无匹配（如 ARM 机器 /proc/cpuinfo 没有 model name）
# 会以退出码 1 杀死整脚本——所有探测命令必须 `|| true` 兜底，单项缺失只影响该项。
cpu_vendor=""
cpu_model=""
cpu_freq=""
if command -v lscpu >/dev/null 2>&1; then
    cpu_vendor=$(lscpu 2>/dev/null | awk -F'[:：]' '/^(Vendor ID|厂商 ID|厂商)/{gsub(/^ +/,"",$2); print $2; exit}' || true)
    cpu_model=$(lscpu 2>/dev/null | awk -F'[:：]' '/^(Model name|型号名称)/{gsub(/^ +/,"",$2); print $2; exit}' || true)
    # 频率优先取最大频率（标称），缺失时取当前运行频率
    cpu_freq=$(lscpu 2>/dev/null | awk -F'[:：]' '/^(CPU max MHz|CPU 最大 MHz|最大 MHz)/{gsub(/^ +/,"",$2); print $2; exit}' || true)
    if [ -z "$cpu_freq" ]; then
        cpu_freq=$(lscpu 2>/dev/null | awk -F'[:：]' '/^CPU MHz/{gsub(/^ +/,"",$2); print $2; exit}' || true)
    fi
fi
if [ -z "$cpu_model" ]; then
    # 兜底 x86：/proc/cpuinfo 的 model name（鲲鹏等 ARM 机器没有该字段）
    cpu_model=$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | sed 's/^[ \t]*//' || true)
fi
if [ -z "$cpu_vendor" ]; then
    cpu_vendor=$(grep -m1 "^vendor_id" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | sed 's/^[ \t]*//' || true)
fi
if [ -z "$cpu_freq" ]; then
    cpu_freq=$(grep -m1 "^cpu MHz" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | sed 's/^[ \t]*//' || true)
fi
cores=$(nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo 2>/dev/null || echo "")
echo "CPU_VENDOR=${cpu_vendor:-未知}"
echo "CPU_MODEL=${cpu_model:-未知}"
echo "CPU_FREQ_MHZ=${cpu_freq:-0}"
echo "CPU_CORES=${cores:-未知}"

# 内存：/proc/meminfo MemTotal（KB → GB），不依赖 free/awk 格式差异
echo "MEM_TOTAL=$(awk '/^MemTotal:/{printf "%.0fG", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 未知)"

# 硬盘：根分区容量与已用（df -h）
echo "DISK_ROOT=$(df -h / 2>/dev/null | awk 'NR==2{print $2"（已用 "$3"）"}' || echo 未知)"
