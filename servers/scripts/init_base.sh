#!/usr/bin/env bash
# NRM 基础初始化脚本：目标机接入 NRM 前的通用准备（幂等，可重复执行）。
# 由 NRM 平台经 SFTP 上传到目标机后以 root 执行（run_init_script）。
#
# 职责：
#   - 创建受管用户组 nrm_managed（所有 NRM 受管用户加入该组）
#   - 创建公告 motd 目录 /etc/motd.d（登录公告写入点）
#   - 补齐基础工具链（curl 等，缺啥装啥，无包管理器时跳过）
# 与 nrm_mgmt.sh（日常用户管理）分工：本脚本只管"接入时的机器初始化"。
set -euo pipefail

NRM_GROUP="nrm_managed"

log() { echo "[init_base] $*" >&2; }

# 1. 受管用户组
if ! getent group "$NRM_GROUP" >/dev/null 2>&1; then
    groupadd "$NRM_GROUP" || { log "创建组 $NRM_GROUP 失败"; exit 3; }
    log "创建组 $NRM_GROUP"
else
    log "组 $NRM_GROUP 已存在"
fi

# 2. motd 公告目录（Ubuntu 经 /etc/motd.d/ 聚合展示登录公告）
mkdir -p /etc/motd.d

# 3. 基础工具链（仅本机有 apt 时安装，失败不阻塞初始化）
if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y -q curl >/dev/null 2>&1 || log "安装 curl 失败（跳过）"
fi

echo "OK init_base"
