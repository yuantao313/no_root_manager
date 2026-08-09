# 部署指南

本文面向部署与运维人员，介绍如何在服务器上安装、配置并运行 NRM。

## 环境要求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)（依赖管理工具）
- SQLite（默认，随 Python 自带；数据量不大无需额外数据库）
- 一台或多台可通过 SSH 管理的目标服务器（需有 sudo 免密权限的管理账户）

## 安装

```bash
# 1. 获取代码并进入目录
git clone <仓库地址> && cd <项目目录>

# 2. 安装依赖
uv sync

# 3. 初始化数据库
uv run python manage.py migrate

# 4. 创建超级管理员
uv run python manage.py createsuperuser
```

## 配置

### SECRET_KEY（必改）

生产环境必须设置独立的 `SECRET_KEY`（环境变量 `NRM_SECRET_KEY` 覆盖默认值）：

```bash
export NRM_SECRET_KEY='一个足够长的随机字符串'
```

> 注意：SECRET_KEY 同时用于敏感字段加密（Fernet）。**变更 SECRET_KEY 会使历史密文无法解密**（凭据、SMTP 密码等需重新配置），上线后请保持不变。

### 域名与回调

设置 `ALLOWED_HOSTS` 为你的域名/IP：

```python
# config/settings.py
ALLOWED_HOSTS = ["nrm.example.com"]
```

### GitCode 登录（可选）

1. 在 [GitCode](https://gitcode.com) 创建 OAuth 应用，获得 Client ID / Client Secret。
2. 系统设置 → GitCode 登录，填写 Client ID / Secret 并保存。
3. 到 GitCode 应用管理页，把**回调地址**配置为系统设置页展示的值（如 `https://nrm.example.com/accounts/allauth/gitcode/login/callback/`），必须完全一致。

### SMTP 邮件（可选，建议开启）

1. 系统设置 → 邮件通知，填写 SMTP 服务器、端口、用户名、密码/授权码、发件人。
2. 加密方式：**465 端口勾选「使用 SSL 直连」**；587/25 端口取消勾选。
3. 按页面三步流程验证并保存：发送验证码 → 验证 → 保存配置。
4. 勾选「启用邮件通知」后生效（新申请、审批结果、开通密码等通知走此邮箱）。

## 启动

```bash
uv run python manage.py runserver 0.0.0.0:8000
```

生产环境建议使用 gunicorn + 反向代理（Nginx）：

```bash
uv run gunicorn config.wsgi:application -b 127.0.0.1:8000 -w 2
```

## 定时任务（cron）

### 撤销当日到期的 sudo 权限（必须）

```cron
0 1 * * * cd /path/to/project && uv run python manage.py expire_sudo
```

说明：sudo 申请"当天有效"，该命令每天执行一次，撤销已过期的 sudo 权限并更新审计记录。

### 静态文件

`uv run python manage.py collectstatic --noinput`（部署时执行；本地 static 目录含自研 css/js，第三方走 CDN）。

## 备份与恢复

### 备份

```bash
cp db.sqlite3 db.sqlite3.bak.$(date +%Y%m%d%H%M%S)
```

建议连同 `.env`（SECRET_KEY 等环境变量）一起备份——**只有数据库没有密钥无法解密敏感字段**。

### 恢复

```bash
# 停止服务 → 用备份覆盖 db.sqlite3 → 重启服务
```

## 常见问题

| 现象 | 处理 |
|------|------|
| 登录被锁定（提示频繁失败） | 等待 15 分钟冷却，或清空 axes 失败记录 |
| 邮件发送失败 `Connection unexpectedly closed` | 465 端口必须勾选"使用 SSL 直连"，587/25 取消 |
| 配置页 `InvalidSignature` | SECRET_KEY 被变更，恢复原密钥或迁移密文 |
| GitCode 登录提示回调地址不匹配 | 系统设置页展示的回调地址，与 GitCode 应用页配置完全一致（含 https/域名/端口/路径） |
| 审批通过提示"未关联目标服务器或凭据" | 在服务器管理中为对应服务器关联可用凭据 |
