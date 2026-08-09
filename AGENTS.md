# AGENTS.md — NRM 项目开发指南

本文件供 AI 编码 Agent 与开发者快速理解项目，指导修改代码。修改前请通读。

## 项目概述

**NRM（No Root Manager）**：面向中小团队（几台服务器、几十到几百用户）的目标机器用户申请与接管管理系统。核心价值：替代"全员 root 登录"的混乱协作，提供**申请 → 审批 → 机器自动开通**的闭环，并强制普通用户 + 资源限额 + 权限审计。

- 技术栈：Python 3.13 / Django 6.1 / SQLite / uv
- 前端：Bootstrap 3.4（CDN）+ crispy-forms；自研 CSS/JS 在 `static/css/app.css`、`static/js/app.js`
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
  models.py        # SystemConfig、Announcement（用户公告）、EmailVerification
  views.py         # 注册/登录/个人中心/密码找回/设置页/解绑
  email_verify.py  # 邮箱验证码服务（生成/发送/校验）
  username_gen.py  # 用户名建议（中文拼音 / 英文首字母规则）
  adapter.py       # allauth SocialAccountAdapter（首次登录走 signup 确认）
  providers/gitcode/  # allauth 自定义 GitCode provider
applications/      # 申请工单（核心业务）
  models.py        # Application、SudoGrant
  views.py         # 申请/审批/开通/撤回联动
  management/      # expire_sudo 管理命令
servers/           # 目标机器管理
  models.py        # Server（含 NPU 字段）、ManagedUser、ServerAdminBinding
  management.py    # SSH 操作（provision/接管/sudo/NPU 检测授权/初始化脚本/公告 motd）
  ssh.py           # paramiko 连接与命令执行
  fields.py        # EncryptedTextField（Fernet 加密字段）
credentials/       # 机器凭据（密码/私钥，加密存储）
notifications/     # 通知（SMTP 邮件 + Webhook）
  services.py      # 发送逻辑（send_email / send_email_with_config）
tests/             # pytest 测试（test_*.py，含 E2E 流程测试）
templates/         # 全部 Bootstrap 3 模板
static/            # 自研 css/js（app.css / app.js）
```

## 权限模型（三层）

| 角色 | 判定 | 能力 |
|------|------|------|
| 超级管理员 | `is_superuser` | 全部：服务器/凭据管理、系统设置、管理员-服务器绑定、审批 |
| 普通管理员 | `is_staff` 且非 superuser | **仅审批**绑定服务器的申请（`Server.visible_to` 过滤） |
| 普通用户 | 登录用户 | 提交/撤回自己的申请、查看进度 |

- 服务器、凭据的所有视图均为 `superuser_required`（`config/decorators.py`）
- 审批视图（申请列表/详情/审批）对普通管理员按 `ServerAdminBinding` 过滤，无权限 404
- **不自定义用户模型**：用户管理复用 Django admin 的 `is_staff`/`is_superuser`；工号等扩展信息存 `UserProfile`

## 认证体系

- **账号登录**：注册（开放）、密码找回（Django ResetView + 自定义邮件）、登录限流（django-axes：15 分钟 5 次失败锁定 username+IP）
- **GitCode OAuth**（django-allauth）：配置存 **SocialApp**（系统设置页维护，含回调地址展示）；首次登录必须走 signup 确认页（注册新账号或绑定已有账号）；业务门禁：
  - GitCode 用户未设姓名不能提交申请（`applications/views.py` 的 SocialAccount 判断）
  - 无本地密码用户解绑前必须先设置密码（`gitcode_unbind` 视图）
- **邮箱验证码**：用户改邮箱、SMTP 配置写库前验证（`accounts/email_verify.py`，10 分钟有效、错误 5 次作废；AJAX 预检 consume=False 不消耗，保存时真正消耗）

## 核心业务流

### 申请 → 审批 → 开通 → 撤回
1. 登录用户提交申请：两种类型（**申请服务器账号**：目标用户名按姓名自动生成；**转移已有账号为受管用户**：填机器已有用户名），填写**申请理由**（无标题），身份/工号从账号自动带入
2. 仅 **NPU 服务器**显示 NPU 卡组选择（npu + npuN），普通服务器无分组选项
3. 管理员审批通过 → `_provision_on_approve` 在机器开通：
   - `provision_user`：建用户 + 随机密码 + `chage -d 0` 强制首改密 + 资源限制（limits.d）
   - NPU 授权：`usermod -aG npu,npuN`（勾选的卡组）
   - 公告：写入目标机 motd（`/etc/motd.d/nrm_notifications`）+ 系统首页展示
   - sudo 授予（`SudoGrant` 审计，当日 23:59:59 失效，`expire_sudo` 命令撤销）
4. 申请人可**撤回**待审批申请（状态 withdrawn）

### SMTP 配置三步验证（写库前）
1. 填配置 + 邮箱 → 发验证码（60 秒冷却，session 记录时间戳）
2. 填验证码 → 点"验证"（`verify_smtp_code`，通过后 `smtp_verified=True`）
3. 点"保存配置"（`save_email_final`，仅已验证状态才入库；session 暂存 `pending_smtp`）

## 安全约定

- **敏感字段加密**：`EncryptedTextField`（Fernet，密钥由 `SECRET_KEY` 派生）用于凭据密码/私钥、SMTP 密码
- **SECRET_KEY 不可变更**：变更后历史密文无法解密（`InvalidSignature`），需数据迁移
- **MAILERS 陷阱**：Django 6.1 的 MAILERS 接管邮件入口，`get_connection(backend)` 不可用；邮件发送必须直接实例化 `EmailBackend` 或走 `send_email_with_config`
- **SSH 提权**：非 root 连接时特权命令自动加 `sudo -n`（`_sudo_wrap` 按管道分段，**不误拆 `||`**）
- **HTML 禁止嵌套 form**：表单内不能再放 `<form>`（如个人中心解绑按钮），否则浏览器忽略内层表单
- **session 序列化**：session 只能存 JSON 可序列化数据（datetime 需转时间戳存）
- **前端静态化**：模板用 `data-*` 属性传 URL/变量给 `static/js/app.js`，JS 不写模板标签

## 开发工作流

```bash
uv sync                       # 安装依赖
uv run python manage.py migrate
uv run pytest                 # 测试（必须全部通过）
uv run ruff check .           # 静态检查（import 排序等，--fix 自动修）
uv run ruff format .          # 代码格式化
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run   # 迁移一致
uv run mkdocs build --strict  # 文档构建（改了 docs/ 必须过）
```

- 新增功能需配套 pytest 用例（`tests/` 目录）
- 静态检查用 **ruff**（不用 pyflakes），配置在 `pyproject.toml [tool.ruff]`
- **测试/验证一律用 pytest 隔离库 + mock SSH/邮件，不连真实机器**（有 E2E 测试机 192.18.142.218，常规开发勿触发）
- **E2E 专项**：`tests/test_e2e_flow.py`（申请→审批→开通→sudo→回收编排）、`tests/test_e2e_frontend.py`（前端操作级：注册→申请→审批→回看）——改动核心业务流后必须跑

## 项目约定（重要）

1. **复用 Django 机制**：不自定义用户模型、不重复造轮子；用户/管理员管理走 Django admin；登录限流用 axes、OAuth 用 allauth、用户名用 pypinyin
2. **不过度设计**：不做需求外的扩展（无企业级认证、无付费、无 cgroups）
3. **分支策略**：日常提交仅本地保存；**开发分支 `dev` 可推送**（供预览/协作）；`main` 正式发布时统一推送并锁定（禁直接 push + CI 必过）
4. **未来计划走 GitHub Issues**：设计方案、UX 改进、待办不写进仓库文档
5. **中文代码支持**：保留现有中文注释与命名习惯；新代码用英文标识符
6. **严禁破坏开发数据库**：验证/调试一律使用 pytest（隔离测试库）或只读检查；**禁止在 `manage.py shell -c` 中对开发库执行 `delete()`/`all().delete()`/清空配置类操作**（曾因此误删用户配置的 SMTP/GitCode 凭据，属 P0 事故）。**页面/接口验证也禁止用 shell 创建数据再删除**（哪怕"创建后清理"也会误删用户已有配置，同样 P0）——一律写成 pytest 临时测试（隔离库）或纯只读检查。确需操作真实数据时先备份 `db.sqlite3`

## 常见陷阱速查

| 现象 | 原因 | 处理 |
|------|------|------|
| 凭据/配置页 `InvalidSignature` | SECRET_KEY 被改 | 恢复原 SECRET_KEY 或密文迁移 |
| `get_connection(backend) is not supported with MAILERS` | 走了 Django 邮件入口 | 直接实例化 EmailBackend |
| 邮件发送 `Connection unexpectedly closed` | 465 端口用了 STARTTLS | 465 用 SSL（use_ssl=True），587/25 用 TLS |
| 邮箱更换后邮箱没变 | 前端 `form.submit()` 不带按钮 name | 提交前手动追加 hidden `name="save_profile"` |
| HTML 按钮无效 | 嵌套 `<form>` | 拆出独立表单 |
| 登录被锁 | axes 15 分钟 5 次失败 | 清 `AccessAttempt` 或等冷却 |
| 迁移交互式提问 EOFError | 非空字段无默认值 | 迁移加 `default` |
| 申请被拦跳个人中心 | GitCode 用户未设姓名 | 先设置个人资料姓名 |
| 验证码被二次校验拦截 | AJAX 预检消耗了验证码 | 预检用 `consume=False`，保存时真正消耗 |
