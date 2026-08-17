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

NRM 支持两种运行模式，由环境变量 `NRM_ENV` 控制：

| 模式 | 触发条件 | 行为 |
|------|----------|------|
| 开发模式（默认） | `NRM_ENV` 未设置或为非 `prod` | `DEBUG=True`、任意域名可访问（`ALLOWED_HOSTS=*`）、CSRF 来源宽松、详细日志（含 SQL/请求/SSH 输出） |
| 部署模式 | `NRM_ENV=prod` | 严格按 `.env.prod` 执行：`DEBUG`/域名/CSRF 缺失即报错，绝不裸奔 |

- 开发模式读取根目录 `.env`，部署模式读取 `.env.prod`（均经 python-dotenv 加载）。
- **优先用环境变量传递敏感配置**（systemd/容器/CI 注入）；`.env`/`.env.prod` 不会覆盖已注入的同名环境变量（`override=False`）。

### 切换到部署模式

```bash
export NRM_ENV=prod
```

部署模式在 `manage.py check` / `runserver` / `gunicorn` 启动时校验必填配置，**缺失直接拒绝启动**：

- `NRM_SECRET_KEY`（必填，禁止沿用开发默认密钥）
- `NRM_ALLOWED_HOSTS`（必填，逗号分隔的域名/IP 列表）

### 配置字段（.env / .env.prod）

| 字段 | 适用 | 说明 | 默认值 |
|------|------|------|--------|
| `NRM_ENV` | 全局 | `prod` 切部署模式，其余为开发 | `dev` |
| `NRM_SECRET_KEY` | 部署必填 | 密钥；同时用于 Fernet 加密敏感字段 | 开发默认 |
| `NRM_DEBUG` | 部署 | 是否开启 DEBUG（布尔） | `False` |
| `NRM_ALLOWED_HOSTS` | 部署必填 | 允许访问的域名/IP，逗号分隔 | 无（缺失报错） |
| `NRM_CSRF_TRUSTED_ORIGINS` | 部署 | CSRF 信任来源，逗号分隔 | 空 |
| `NRM_SECURE_SSL_REDIRECT` | 部署 | 是否把 HTTP 重定向到 HTTPS | `True` |
| `NRM_SECURE_HSTS_SECONDS` | 部署 | HSTS 有效期；确认 HTTPS 稳定后再保持长周期 | `31536000` |
| `NRM_SECURE_HSTS_INCLUDE_SUBDOMAINS` | 部署可选 | HSTS 是否覆盖子域名 | `False` |
| `NRM_SECURE_HSTS_PRELOAD` | 部署可选 | 是否声明 HSTS preload；启用前需确认域名满足预加载要求 | `False` |
| `NRM_TRUST_X_FORWARDED_PROTO` | 反向代理部署可选 | 仅当可信代理会覆盖 `X-Forwarded-Proto` 时启用 | `False` |
| `NRM_GITCODE_CALLBACK_BASE_URL` | 全局 | GitCode OAuth 回调基准地址（无默认值，务必在 .env/.env.prod 中配置） | 空（由系统设置页的站点地址兜底） |
| `NRM_SYNC_NPU` | 开发 | 设为 `1` 时开发模式也启动 NPU 状态同步（部署模式默认开启） | 关 |
| `NRM_LOG_LEVEL` | 部署 | 日志级别（DEBUG/INFO/WARNING…） | `INFO` |
| `NRM_LOG_FILE` | 部署可选 | 设置后追加滚动文件日志（10MB×5） | 无（仅控制台） |
| `NRM_DB_PATH` | 全局 | 自定义 SQLite 路径 | 默认 `db.sqlite3` |

> 注意：`NRM_SECRET_KEY` 同时用于敏感字段加密（Fernet）。**变更 SECRET_KEY 会使历史密文无法解密**（凭据、SMTP 密码等需重新配置），上线后请保持不变。

### 部署模式示例（.env.prod）

```bash
NRM_ENV=prod
NRM_SECRET_KEY='一个足够长的随机字符串'
NRM_DEBUG=False
NRM_ALLOWED_HOSTS=nrm.example.com,www.nrm.example.com,localhost
NRM_CSRF_TRUSTED_ORIGINS=https://nrm.example.com
NRM_SECURE_SSL_REDIRECT=True
NRM_SECURE_HSTS_SECONDS=31536000
NRM_GITCODE_CALLBACK_BASE_URL=https://nrm.example.com
NRM_LOG_LEVEL=INFO
NRM_LOG_FILE=/var/log/nrm/nrm.log
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

### 服务器 SSH 主机指纹

新增或编辑服务器时必须保存经管理员核对的 OpenSSH `SHA256:` 主机指纹。系统不会自动信任未知主机，指纹不匹配时会拒绝连接；升级后已有服务器也必须补录指纹，才能继续批准开通申请。

### Webhook 出站限制

邮件 Webhook、全局 Webhook 和管理员个人 Webhook 仅允许公网 HTTPS 地址。系统会拒绝回环、内网、链路本地和保留地址，并在请求时固定已校验的公网 IP，避免 DNS 重绑定导致 SSRF。

## 启动

```bash
# 开发模式（默认）：直接启动即可，宽松 + 详细日志
uv run python manage.py runserver 0.0.0.0:8000

# 部署模式：先切环境再启动，严格按 .env.prod 执行
export NRM_ENV=prod
uv run python manage.py runserver 0.0.0.0:8000
```

生产环境建议使用 gunicorn + 反向代理（Nginx）：

```bash
export NRM_ENV=prod
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

建议连同 `.env` / `.env.prod`（SECRET_KEY 等环境变量）一起备份——**只有数据库没有密钥无法解密敏感字段**。

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
