# NRM · No Root Manager

NRM 是面向中小团队的轻量服务器账号申请与接管平台。它解决的不是资产监控或自动化运维，而是一个明确闭环：**用户申请 → 管理员审批 → 通过 SSH 在目标机执行账号操作 → 留下平台记录与通知**。

## 当前能力

| 能力 | 当前实现 |
|------|----------|
| 账号与登录 | Django 账号注册/登录/找回密码；可选 GitCode OAuth；django-axes 登录限流 |
| 申请工单 | 新建账号、接管已有账号、申请 sudo/docker 组、申请服务器管理员权限；支持撤回、筛选和审批意见 |
| 审批边界 | 普通管理员仅审批绑定服务器；root 等价权限只允许超级管理员批准；越权访问返回 403/404 |
| 机器操作 | Paramiko SSH；主机指纹固定；创建、接管、锁定/解锁、用户组调整、基础初始化、motd 公告 |
| 设备概览 | 服务器详情按需读取 CPU、内存、磁盘信息并使用短期缓存与最近快照降级 |
| 服务器与凭据 | 共用一个管理入口和两个页签；服务器保留新增/编辑/测试及新账号默认组，凭据 CRUD 复用 Django Admin |
| 通知 | SMTP 或邮件 Webhook；申请事件支持全局/管理员 Webhook；出站地址限制为公网 HTTPS |

审批后的 SSH 操作在请求内完成，确保没有持久任务队列时不会因 Web 进程退出而丢失。失败会记录原因，并在工单详情提供“重试开通”。非关键通知仍采用进程内后台发送，失败只记录日志。

## 明确边界

- 不提供 NPU/GPU 管理、资源配额、监控告警、计费、企业级 IAM 或移动端。
- 默认使用 SQLite，适合几台服务器、几十到几百用户的单实例部署。
- 不引入 Redis、Celery 等额外基础设施；需要多实例或高并发时，应重新评估数据库、共享缓存和任务队列。

## 快速开始

```bash
uv sync --locked
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

访问 <http://127.0.0.1:8000/>。完整步骤见 [快速开始](docs/quickstart.md) 和 [部署指南](docs/deploy.md)。

## 开发检查

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run mkdocs build --strict
```
