# NRM · No Root Manager

面向中小团队（几台服务器、几十到几百用户）的**目标机器用户申请与接管管理系统**。

## 背景

服务器以前靠"全员 root 登录、谁都能改"来协作，容易互相踩踏、乱改环境、资源被单个用户耗尽。NRM 提供一个轻量平台：**用户匿名提交申请 → 管理员审批（或按需自动开通）→ 系统在目标机器上自动创建普通用户并分配资源限额**，全程无需人工登录机器操作。

> 设计原则：**不过度设计，也不能懒惰**。面向的场景是"用户没有少到可以手动管理，也没有多到必须上企业级平台"——所以不包含企业级认证、付费计费等重型能力，聚焦账号开通、权限审计与资源限制。

## 核心能力

| 能力 | 说明 |
|------|------|
| 用户申请 | 匿名提交，填写姓名/用户名/邮箱/工号，可选目标服务器、使用截止时间、sudo 权限、目录迁移 |
| 审批开通 | 管理员审批通过后自动在机器建用户、生成随机密码并发邮件；支持按服务器开启自动审批 |
| 资源限制 | 开通时按服务器配置写入 `/etc/security/limits.d/`（nproc / nofile），防止单个用户耗尽资源 |
| 权限审计 | sudo 授予严格记录（授予人/时间/当日失效），`expire_sudo` 命令次日自动撤销 |
| 机器接管 | 读取目标机器 `nrm_managed` 组成员并接管，所有受管用户统一纳入管理 |
| 通知 | SMTP 邮件（新申请/审批结果/密码下发）+ Webhook（申请创建/审批事件） |
| 用户名建议 | 按姓名自动生成候选用户名（复姓/多音字排列组合），供申请时一键选择 |

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

访问 http://127.0.0.1:8000/ ，普通用户直接提交申请；管理员登录 `/accounts/login/` 审批。

## 测试与检查

```bash
uv run pytest          # 单元测试
uv run pyflakes .      # 静态检查（忽略 .venv/）
uv run python manage.py check
```

## 文档

完整文档见 [docs/](docs/)，或运行 `uv run mkdocs serve` 本地预览：
- [快速开始](docs/quickstart.md)
- [架构设计](docs/architecture.md)
- [运维手册](docs/operations.md)

## 技术栈

- Python 3.13 / Django 6.1 / SQLite
- paramiko（SSH 执行）、pypinyin（用户名拼音）、cryptography（密钥加密存储）
- Bootstrap 3.4（前端）、mkdocs（文档）
