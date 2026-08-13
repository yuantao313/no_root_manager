"""服务器脚本注册：仓库内置脚本统一在此登记路径。

- ``INIT_BASE_SCRIPT``：基础初始化（所有服务器接入时执行）
- ``INIT_NPU_SCRIPT``：Ascend NPU 初始化（仅 NPU 服务器执行）
- ``HOST_INFO_SCRIPT``：主机信息采集（CPU/内存/硬盘，所有服务器）
- ``NPU_INFO_SCRIPT``：NPU 卡信息采集（Ascend 型号/内存，仅 NPU 服务器）
- ``GPU_INFO_SCRIPT``：GPU 卡信息采集（预留，暂不实现）
- ``MGMT_SCRIPT``：日常用户管理脚本（建用户/接管/锁户/授权等）

设备采集按类型拆分独立脚本维护（host/npu/gpu 互不干扰），
新增设备类型时另建 ``*_info.sh`` 并在 ``servers/devices.py`` 中按类型执行。
"""

from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent

INIT_BASE_SCRIPT = str(_SCRIPTS_DIR / "init_base.sh")
INIT_NPU_SCRIPT = str(_SCRIPTS_DIR / "init_ascend_npu.sh")
HOST_INFO_SCRIPT = str(_SCRIPTS_DIR / "host_info.sh")
NPU_INFO_SCRIPT = str(_SCRIPTS_DIR / "npu_info.sh")
GPU_INFO_SCRIPT = str(_SCRIPTS_DIR / "gpu_info.sh")
MGMT_SCRIPT = str(_SCRIPTS_DIR / "nrm_mgmt.sh")
