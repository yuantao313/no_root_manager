# NRM · No Root Manager

面向中小团队（几台服务器、几十到几百用户）的**目标机器用户申请与接管管理系统**。

## 背景

服务器以前靠"全员 root 登录、谁都能改"来协作，容易互相踩踏、乱改环境、资源被单个用户耗尽。NRM 提供一个轻量平台：**用户登录后提交申请 → 管理员审批（或按需自动开通）→ 系统在目标机器上自动创建普通用户并分配资源限额**，全程无需人工登录机器操作。

> 设计原则：**不过度设计，也不能懒惰**。面向的场景是"用户没有少到可以手动管理，也没有多到必须上企业级平台"——所以不包含企业级认证、付费计费等重型能力，聚焦账号开通、权限审计与资源限制。

## 核心能力

| 能力 | 说明 |
|------|------|
| 用户申请 | 登录提交，可选"申请服务器账号 / 转移已有账号为受管用户 / 申请用户组 / 申请平台管理员"等类型；身份信息（姓名/工号/目标用户名）从账号自动带入，无需重复填写 |
| 审批开通 | 管理员审批通过后自动在机器建用户、生成随机密码并发邮件；NPU 服务器可勾选算力卡组，开通时自动授权 |
| 资源限制 | 开通时按服务器配置写入 `/etc/security/limits.d/`（nproc / nofile / 内存等），防止单个用户耗尽资源 |
| 机器接管 | 一键接管/禁用/启用目标机器用户，所有受管用户与系统账号一对一绑定 |
| 设备信息 | 申请界面展示目标机 NPU 卡（型号/内存）、CPU、内存、硬盘等设备信息，按需查询并缓存 |
| 资源监控 | 同步采集受管用户的磁盘/内存/CPU 使用并展示（含同步时间） |
| 通知 | SMTP 邮件（新申请/审批结果/密码下发）+ Webhook（申请创建/审批事件） |
| 登录方式 | 账号密码 + GitCode OAuth（支持绑定已有账号/注册新账号） |

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 数据库迁移
uv run python manage.py migrate

# 3. 创建管理员
uv run python manage.py createsuperuser

# 4. 启动
uv run python manage.py runserver
```

访问 http://127.0.0.1:8000/ ，注册登录后提交申请；管理员登录 `/accounts/login/` 审批。

## 测试与检查

```bash
uv run pytest              # 单元测试（含 E2E 流程测试）
uv run ruff check .        # 静态检查
uv run python manage.py check
```

## 文档

完整文档见 [docs/](docs/)，或运行 `uv run mkdocs serve` 本地预览：
- [使用指南](docs/usage/index.md)：普通用户 / 管理员 / 超级管理员操作说明
- [快速开始](docs/quickstart.md)
- [部署指南](docs/deploy.md)：安装、配置、启动、定时任务、备份恢复
- [架构设计](docs/architecture.md)
- [运维手册](docs/operations.md)

## 技术栈

- Python 3.13 / Django 6.1 / SQLite
- paramiko（SSH 执行）、pypinyin（用户名拼音）、cryptography（密钥加密存储）
- Bootstrap 3.4（前端）、mkdocs（文档）
