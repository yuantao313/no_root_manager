#!/usr/bin/env bash
# NRM GPU 卡信息采集脚本：NVIDIA 设备型号与显存（预留，暂不实现）。
# 由 NRM 平台经 SFTP 上传后以 root 一次性执行（servers/devices.py）。
#
# 独立于主机信息（host_info.sh）与 NPU（npu_info.sh），各设备类型互不干扰。
# 当前仅输出预留占位；未来实现时在此加 nvidia-smi / pynvml 探测，
# 输出约定保持：
#   GPU_CARD <设备号> <显存GB> <型号…>   （每卡一行；型号可能含空格，故放最后）
#   GPU_ERROR=<说明>                       （可选，探测失败时）
set -euo pipefail

echo "GPU_ERROR=GPU 检测暂未实现（预留）"
