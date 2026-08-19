# 部署指南

本文描述 NRM 当前支持的单实例部署：一个 Gunicorn worker、多线程、SQLite、本机 LocMem 缓存。不需要 Redis 或独立任务服务。

## 环境

- Linux、Python 3.13+
- uv
- 可写的 SQLite 数据目录
- 能通过 SSH 管理目标机的 root 或 `sudo -n` 账号

## 安装

```bash
git clone <仓库地址>
cd <项目目录>
uv sync --locked
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py collectstatic --noinput
```

## 配置

`NRM_ENV=dev` 读取 `.env`，`NRM_ENV=prod` 读取 `.env.prod`；已注入的环境变量优先，不会被文件覆盖。

| 变量 | 生产要求 | 说明 |
|------|----------|------|
| `NRM_ENV` | 必填为 `prod` | 启用生产安全设置 |
| `NRM_SECRET_KEY` | 必填 | Django 签名及敏感字段加密根密钥；上线后不可随意轮换 |
| `NRM_ALLOWED_HOSTS` | 必填 | 逗号分隔的域名/IP |
| `NRM_CSRF_TRUSTED_ORIGINS` | 使用跨站 Origin 时填写 | 完整来源，例如 `https://nrm.example.com` |
| `NRM_DB_PATH` | 可选 | SQLite 路径；相对路径以项目根目录为基准 |
| `NRM_DEBUG` | 可选，默认 `False` | 生产环境不要开启 |
| `NRM_SECURE_SSL_REDIRECT` | 可选，默认 `True` | HTTPS 反代部署保持开启 |
| `NRM_SECURE_HSTS_SECONDS` | 可选，默认 `31536000` | 首次部署可先设较小值验证 HTTPS |
| `NRM_SECURE_HSTS_INCLUDE_SUBDOMAINS` | 可选，默认 `False` | 确认所有子域均为 HTTPS 后再启用 |
| `NRM_SECURE_HSTS_PRELOAD` | 可选，默认 `False` | 满足浏览器 preload 条件后再启用 |
| `NRM_TRUST_X_FORWARDED_PROTO` | 可信反代场景可选 | 仅当代理会覆盖该请求头时启用 |
| `NRM_GITCODE_CALLBACK_BASE_URL` | GitCode 登录可选 | OAuth 回调基准地址；系统设置可覆盖 |
| `NRM_LOG_LEVEL` | 可选，默认 `INFO` | Python 日志级别 |
| `NRM_LOG_FILE` | 可选 | 启用 10 MiB × 5 的滚动文件日志 |

示例：

```dotenv
NRM_ENV=prod
NRM_SECRET_KEY=请使用密码管理器生成并保存的稳定随机值
NRM_ALLOWED_HOSTS=nrm.example.com
NRM_CSRF_TRUSTED_ORIGINS=https://nrm.example.com
NRM_DB_PATH=/var/lib/nrm/db.sqlite3
NRM_GITCODE_CALLBACK_BASE_URL=https://nrm.example.com
NRM_LOG_FILE=/var/log/nrm/nrm.log
```

部署前执行：

```bash
NRM_ENV=prod uv run python manage.py check --deploy
```

## 启动

开发服务器只用于本机开发：

```bash
uv run python manage.py runserver
```

局域网开发若绑定 `0.0.0.0`，必须在 `.env` 显式填写访问 IP/域名和 Origin；开发模式不再默认接受任意 Host。

生产使用项目依赖中已锁定的 Gunicorn：

```bash
NRM_ENV=prod uv run gunicorn config.wsgi:application \
  --bind 127.0.0.1:8000 \
  --workers 1 \
  --threads 4 \
  --timeout 180
```

保持一个 worker：当前 select2 和 SSH 查询缓存使用进程内缓存。线程允许一个审批请求等待 SSH 时仍能处理其他页面请求。前置 Nginx/Caddy 负责 TLS、静态文件和请求体限制。

## 升级

```bash
uv sync --locked
uv run python manage.py migrate
uv run python manage.py collectstatic --noinput
NRM_ENV=prod uv run python manage.py check --deploy
```

迁移前先按[运维手册](operations.md)备份数据库和 `NRM_SECRET_KEY`，验证通过后再重启服务。

第三方 CSS、JavaScript 和 Bootstrap 字体均从本机静态目录提供。`runserver` 启动前和 `collectstatic` 收集前会自动校验固定版本文件；仅在文件缺失或损坏时从锁定的 jsDelivr 地址重新下载，并在 SHA-256 校验通过后替换，无需额外命令。运行中的 Web 请求不会临时联网下载资源。排障时仍可单独运行 `uv run python scripts/ensure_vendor_assets.py`。

## 可选集成

- GitCode OAuth：在系统设置保存 Client ID/Secret，并把页面展示的回调地址原样配置到 GitCode。
- SMTP：465 使用 SSL；587/25 使用 STARTTLS。配置保存前必须完成验证码验证。
- Webhook：只接受公网 HTTPS 地址；系统会拒绝内网、回环、保留地址和带用户信息的 URL。
- SSH：必须录入管理员核验过的 OpenSSH `SHA256:` 主机指纹，否则不能审批或重试机器操作。

## 扩容边界

出现持续 SQLite 锁竞争、需要多个 Web 实例或必须保证通知送达时，说明已超出当前单实例边界。届时应整体评估 PostgreSQL、共享缓存和持久任务队列，而不是只增加 Gunicorn worker。
