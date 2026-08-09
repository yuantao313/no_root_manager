# AGENTS.md — NRM 项目开发指南

本文件供 AI 编码 Agent 与开发者快速理解项目，指导修改代码。修改前请通读。

## 项目概述

**NRM（No Root Manager）**：面向中小团队（几台服务器、几十到几百用户）的目标机器用户申请与接管管理系统。核心价值：替代"全员 root 登录"的混乱协作，提供**申请 → 审批 → 机器自动开通**的闭环，并强制普通用户 + 资源限额 + 权限审计。

- 技术栈：Python 3.13 / Django 6.1 / SQLite / uv
- 前端：Bootstrap 3.4（本地 vendor，不用 CDN）+ crispy-forms
- SSH 执行：paramiko；密码/密钥加密：cryptography（Fernet）
- 第三方登录：django-allauth（GitCode OAuth）；登录防爆破：django-axes

## 模块结构

```
config/            # Django 项目配置
  settings.py      # 所有配置（含 AXES/allauth/CRISPY）
  urls.py          # 根路由（/ 跳转登录或我的申请）
  decorators.py    # staff_required / superuser_required
  views.py         # 根路由 index 视图
accounts/          # 用户、认证、系统设置
  models.py        # SystemConfig（GitCode 配置）、EmailVerification（验证码）
  views.py         # 注册/登录/个人中心/密码找回/设置页/解绑
  email_verify.py  # 邮箱验证码服务（生成/发送/校验）
  username_gen.py  # 用户名建议（pypinyin 复姓/多音字）
  providers/gitcode/  # allauth 自定义 GitCode provider
applications/      # 申请工单（核心业务）
  models.py        # Application、SudoGrant
  views.py         # 申请/审批/开通联动
  management/      # expire_sudo 管理命令
servers/           # 目标机器管理
  models.py        # Server、ManagedUser、ServerAdminBinding
  management.py    # SSH 机器操作（provision/接管/sudo/迁移/资源限制）
  ssh.py           # paramiko 连接与命令执行
  fields.py        # EncryptedTextField（Fernet 加密字段）
credentials/       # 机器凭据（密码/私钥，加密存储）
notifications/     # 通知（SMTP 邮件 + Webhook）
  services.py      # 发送逻辑（send_email / send_email_with_config）
tests/             # pytest 测试（test_*.py）
templates/         # 全部 Bootstrap 3 模板
```

## 权限模型（三层）

| 角色 | 判定 | 能力 |
|------|------|------|
| 超级管理员 | `is_superuser` | 全部：服务器/凭据管理、系统设置、管理员-服务器绑定、审批 |
| 普通管理员 | `is_staff` 且非 superuser | **仅审批**绑定服务器的申请（`Server.visible_to` 过滤） |
| 普通用户 | 登录用户 | 提交/查看自己的申请 |

- 服务器、凭据的所有视图均为 `superuser_required`（`config/decorators.py`）
- 审批视图（申请列表/详情/审批）对普通管理员按 `ServerAdminBinding` 过滤，无权限 404
- **不自定义用户模型**：用户管理复用 Django admin 的 `is_staff`/`is_superuser`

## 认证体系

- **账号登录**：注册（开放）、密码找回（Django ResetView + 自定义邮件）、登录限流（django-axes：15 分钟 5 次失败锁定 username+IP）
- **GitCode OAuth**（django-allauth）：自定义 provider（`accounts/providers/gitcode/`），uid 映射为 24 位 hex 用户 id；登录/绑定/解绑走 allauth 机制，保留业务门禁：
  - GitCode 用户未设姓名不能提交申请（`applications/views.py` 的 SocialAccount 判断）
  - 无本地密码用户解绑前必须先设置密码（`gitcode_unbind` 视图）
- **邮箱验证码**：用户改邮箱、SMTP 配置写库前验证（`accounts/email_verify.py`，10 分钟有效、错误 5 次作废）

## 核心业务流

### 申请 → 审批 → 开通
1. 登录用户提交申请（身份信息从账号获取，不手填姓名/邮箱）
2. 管理员审批通过 → `_provision_on_approve` 在机器开通：
   - `provision_user`：建用户 + 随机密码 + `chage -d 0` 强制首改密 + 资源限制（limits.d）
   - 目录迁移（`migrate_home_dir`，`mv -T` 防嵌套、chown 失败回滚）
   - sudo 授予（`SudoGrant` 审计，当日 23:59:59 失效，`expire_sudo` 命令撤销）

### SMTP 配置三步验证（写库前）
1. 填配置 + 邮箱 → 发验证码（60 秒冷却，session 记录时间戳）
2. 填验证码 → 点"验证"（`verify_smtp_code`，通过后 `smtp_verified=True`）
3. 点"保存配置"（`save_email_final`，仅已验证状态才入库；session 暂存 `pending_smtp`）

## 安全约定

- **敏感字段加密**：`EncryptedTextField`（Fernet，密钥由 `SECRET_KEY` 派生）用于凭据密码/私钥、SMTP 密码、GitCode client_secret
- **SECRET_KEY 不可变更**：变更后历史密文无法解密（`InvalidSignature`），需数据迁移
- **MAILERS 陷阱**：Django 6.1 的 MAILERS 接管邮件入口，`get_connection(backend)` 不可用；邮件发送必须直接实例化 `EmailBackend` 或走 `send_email_with_config`
- **SSH 提权**：非 root 连接时特权命令自动加 `sudo -n`（`_sudo_wrap` 按管道分段，**不误拆 `||`**）
- **HTML 禁止嵌套 form**：表单内不能再放 `<form>`（如个人中心解绑按钮），否则浏览器忽略内层表单
- **session 序列化**：session 只能存 JSON 可序列化数据（datetime 需转时间戳存）

## 开发工作流

```bash
uv sync                       # 安装依赖
uv run python manage.py migrate
uv run pytest                 # 测试（必须全部通过）
uv run ruff check .           # 静态检查（import 排序等，--fix 自动修）
uv run ruff format .          # 代码格式化
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run   # 迁移一致
```

- 新增功能需配套 pytest 用例（`tests/` 目录）
- 静态检查用 **ruff**（不用 pyflakes），配置在 `pyproject.toml [tool.ruff]`
- 测试中用户创建、SSH 调用、邮件发送均用 mock / patch，**不连真实机器**
- 有真实服务器 E2E 测试机（192.18.142.218），但常规开发勿触发

## 项目约定（重要）

1. **复用 Django 机制**：不自定义用户模型、不重复造轮子；用户/管理员管理走 Django admin
2. **不过度设计**：不做需求外的扩展（无企业级认证、无付费、无 cgroups）
3. **发布前本地提交**：正式 release 宣布前提交仅本地保存（不推送远程），发布时统一推送并锁定 master
4. **未来计划走 GitHub Issues**：设计方案、UX 改进、待办不写进仓库文档
5. **中文代码支持**：保留现有中文注释与命名习惯；新代码用英文标识符
6. **严禁破坏开发数据库**：验证/调试一律使用 pytest（隔离测试库）或只读检查；**禁止在 `manage.py shell -c` 中对开发库执行 `delete()`/`all().delete()`/清空配置类操作**（曾因此误删用户配置的 SMTP/GitCode 凭据，属 P0 事故）。确需操作真实数据时先备份 `db.sqlite3`

## 常见陷阱速查

| 现象 | 原因 | 处理 |
|------|------|------|
| 凭据/配置页 `InvalidSignature` | SECRET_KEY 被改 | 恢复原 SECRET_KEY 或密文迁移 |
| `get_connection(backend) is not supported with MAILERS` | 走了 Django 邮件入口 | 直接实例化 EmailBackend |
| HTML 按钮无效 | 嵌套 `<form>` | 拆出独立表单 |
| 登录被锁 | axes 15 分钟 5 次失败 | 清 `AccessAttempt` 或等冷却 |
| 迁移交互式提问 EOFError | 非空字段无默认值 | 迁移加 `default` |
| 申请被拦跳个人中心 | GitCode 用户未设姓名 | 先设置个人资料姓名 |
