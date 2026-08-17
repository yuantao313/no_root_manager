"""服务器脚本注册：仓库内置脚本统一在此登记路径。

- ``INIT_BASE_SCRIPT``：基础初始化（所有服务器接入时执行）
- ``HOST_INFO_SCRIPT``：主机信息采集（CPU/内存/硬盘，所有服务器）
- ``MGMT_SCRIPT``：日常用户管理脚本（建用户/接管/锁户/授权等）
"""

from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent

INIT_BASE_SCRIPT = str(_SCRIPTS_DIR / "init_base.sh")
HOST_INFO_SCRIPT = str(_SCRIPTS_DIR / "host_info.sh")
MGMT_SCRIPT = str(_SCRIPTS_DIR / "nrm_mgmt.sh")
