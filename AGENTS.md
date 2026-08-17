# AGENTS.md — NRM 项目开发指南

本文件供 AI 编码 Agent 与开发者快速理解项目，指导修改代码。修改前请通读。

## 项目概述

**NRM（No Root Manager）**：面向中小团队（几台服务器、几十到几百用户）的目标机器用户申请与接管管理系统。核心价值：替代"全员 root 登录"的混乱协作，提供**申请 → 审批 → 机器自动开通**的闭环，并强制普通用户 + 资源限额 + 权限审计。

- 技术栈：Python 3.13 / Django 6.1 / SQLite / uv
- 前端：Bootstrap 3.4（CDN）+ crispy-forms；自研 CSS/JS 在 `static/css/app.css`、`static/js/app.js`
- SSH 执行：paramiko；密码/密钥加密：cryptography（Fernet）
- 第三方登录：django-allauth（GitCode OAuth）；登录防爆破：django-axes
- 运行模式：`NRM_ENV=dev|prod` 切换，配置从 `.env` / `.env.prod` 加载（python-dotenv）

## 模块结构

```
config/            # Django 项目配置
  settings.py      # 所有配置（含 AXES/allauth/CRISPY、运行模式）
  urls.py          # 根路由（/ 跳转登录或我的申请）
  decorators.py    # staff_required / superuser_required
accounts/          # 用户、认证、系统设置
  models.py        # SystemConfig、Announcement（公告，markdown 单例）、EmailVerification
  views.py         # 注册/登录/个人中心/密码找回/设置页/解绑
  email_verify.py  # 邮箱验证码服务（生成/发送/校验）
  markdown_convert.py  # 公告 markdown 子集转换器（→HTML 首页 / →ANSI motd）
  adapter.py       # allauth SocialAccountAdapter（首次登录走 signup 确认）
  providers/gitcode/  # allauth 自定义 GitCode provider
applications/      # 申请工单（核心业务）
  models.py        # Application（申请单）
  views.py         # 申请/审批/开通/撤回联动
servers/           # 目标机器管理
  models.py        # Server、MachineUserBinding、ServerAdminBinding
  management.py    # SSH 操作（provision/接管/锁定/授权/公告 motd）
  devices.py       # 基础设备信息统一查询（CPU/内存/硬盘，TTL 缓存）
  scripts/         # 目标机脚本（SFTP 上传 root 执行，见下）
  ssh.py           # paramiko 连接与命令执行（run_script 上传执行）
  fields.py        # EncryptedTextField（Fernet 加密字段）
credentials/       # 机器凭据（密码/私钥，加密存储；管理界面复用 Django admin）
notifications/     # 通知（SMTP 邮件 + Webhook）
  services.py      # 发送逻辑（send_email / send_email_with_config）
tests/             # pytest 测试（test_*.py）
templates/         # 全部 Bootstrap 3 模板
static/            # 自研 css/js（app.css / app.js）
```

### 目标机脚本（servers/scripts/，独立维护、仓库内置）

| 脚本 | 职责 | 执行时机 |
|------|------|----------|
| `nrm_mgmt.sh` | 日常用户管理（建用户/接管/锁定/sudo 授权） | 各操作按子命令调用 |
| `init_base.sh` | 基础初始化（受管组/motd 目录/工具链） | 服务器接入时 |
| `host_info.sh` | 基础设备信息采集（CPU、内存、硬盘），root 一次执行、结构化输出 | 设备信息查询（TTL 缓存） |

## 权限模型（三层）

| 角色 | 判定 | 能力 |
|------|------|------|
| 超级管理员 | `is_superuser` | 全部：服务器/凭据管理、系统设置、管理员-服务器绑定、审批 |
| 普通管理员 | `is_staff` 且非 superuser | **仅审批**绑定服务器的申请（`Server.visible_to` 过滤） |
| 普通用户 | 登录用户 | 提交/撤回自己的申请、查看进度 |

- 服务器视图均为 `superuser_required`；凭据管理复用 Django admin，仅超级管理员拥有模型权限
- 审批视图（申请列表/详情/审批）对普通管理员按 `ServerAdminBinding` 过滤，无权限 404
- **不自定义用户模型**：用户管理复用 Django admin 的 `is_staff`/`is_superuser`；工号等扩展信息存 `UserProfile`

## 认证体系

- **账号登录**：注册（开放）、密码找回（Django ResetView + 自定义邮件）、登录限流（django-axes：15 分钟 5 次失败锁定 username+IP）
- **GitCode OAuth**（django-allauth）：配置存 **SocialApp**（系统设置页维护，含回调地址展示）；首次登录必须走 signup 确认页（注册新账号或绑定已有账号）；业务门禁：
  - GitCode 用户未设姓名不能提交申请
  - 无本地密码用户解绑前必须先设置密码（`gitcode_unbind` 视图）
- **邮箱验证码**：用户改邮箱、SMTP 配置写库前验证（10 分钟有效、错误 5 次作废；AJAX 预检 consume=False 不消耗，保存时真正消耗）

## 核心业务流

### 申请 → 审批 → 开通 → 撤回
1. 登录用户提交申请：四种类型（**申请服务器账号 / 转移已有账号为受管用户 / 申请用户组 / 申请平台管理员**），填**申请理由**；身份/工号从账号自动带入
2. 管理员审批通过 → `_bg_provision` 后台在机器开通：
   - `provision_user`：建用户 + 随机密码 + `chage -d 0` 强制首改密
   - 账号归属写入 `MachineUserBinding`
   - 公告：写入目标机 motd（`/etc/motd.d/nrm_notifications`）+ 系统首页展示
   - 平台管理员类型：直接授予 sudo（不建账号，无审计表）
3. 申请人可**撤回**待审批申请（状态 withdrawn）

### 系统公告（markdown）
- 公告为**单例**：`content` 存 markdown 源码（`# 标题 / **加粗** / *斜体* / {red}颜色{/red} / [链接](url)`）
- 首页公告栏用 `markdown_to_html` 渲染（HTML 仅受控标签，模板 `|safe`）；服务器 motd 用 `markdown_to_ansi`（h1 亮黄/h2 暗黄/h3 灰）
- 设置页用 textarea + 快捷按钮插入控制符（非富文本编辑器）

### 设备信息（servers/devices.py）
- 所有设备信息查询统一走 `get_device_info(server)`：按 server.pk 做 **TTL 缓存**，避免每次访问 SSH 探测
- 实际采集由 `host_info.sh` 在目标机 **root 一次性执行**，Python 侧解析 key=value 输出
- CPU 优先使用 lscpu（兼容鲲鹏无 model name），同时采集内存与根分区用量

### SMTP 配置三步验证（写库前）
1. 填配置 + 邮箱 → 发验证码（60 秒冷却，session 记录时间戳）
2. 填验证码 → 点"验证"（`verify_smtp_code`，通过后 `smtp_verified=True`）
3. 点"保存配置"（`save_email_final`，仅已验证状态才入库；session 暂存 `pending_smtp`）

## 安全约定

- **敏感字段加密**：`EncryptedTextField`（Fernet，密钥由 `SECRET_KEY` 派生）用于凭据密码/私钥、SMTP 密码
- **SECRET_KEY 不可变更**：变更后历史密文无法解密（`InvalidSignature`），需数据迁移
- **MAILERS 陷阱**：Django 6.1 的 MAILERS 接管邮件入口，`get_connection(backend)` 不可用；邮件发送必须直接实例化 `EmailBackend` 或走 `send_email_with_config`
- **SSH 提权**：非 root 连接时特权命令自动加 `sudo -n`（`_sudo_wrap` 按管道分段，**不误拆 `||`**）；目标机脚本统一经 SFTP 上传后 root 执行
- **SSH 主机身份**：服务器必须保存管理员核对过的 OpenSSH `SHA256:` 指纹；禁止 `AutoAddPolicy`，禁止隐式使用本机 SSH agent/密钥
- **Webhook 出站**：仅允许公网 HTTPS，拒绝内网/回环/保留地址；发送时固定到同次 DNS 校验得到的公网 IP
- **脚本必须 LF 行尾**：`.gitattributes` 锁定 `*.sh text eol=lf`，CRLF 会让 Linux 目标机 bash 报错
- **HTML 禁止嵌套 form**：表单内不能再放 `<form>`，否则浏览器忽略内层表单
- **session 序列化**：session 只能存 JSON 可序列化数据（datetime 需转时间戳存）
- **前端静态化**：模板用 `data-*` 属性传 URL/变量给 `static/js/app.js`，JS 不写模板标签
- **安全输出**：公告 markdown 转 HTML 只产生受控标签；链接协议白名单（http/https/mailto），危险协议只渲染文字

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
- **测试/验证一律用 pytest 隔离库 + mock SSH/邮件，不连真实机器**；改动核心业务流后跑 `tests/test_e2e_flow.py`

## 项目约定（重要）

1. **复用 Django 机制**：不自定义用户模型、不重复造轮子；用户/管理员管理走 Django admin；登录限流用 axes、OAuth 用 allauth、用户名用 pypinyin
2. **不过度设计**：不做需求外的扩展（无企业级认证、无付费、无 cgroups）
3. **分支策略**：日常提交仅本地保存；**开发分支 `dev` 可推送**（供预览/协作）；`main` 正式发布时统一推送并锁定（禁直接 push + CI 必过）
4. **未来计划走 GitHub Issues**：设计方案、UX 改进、待办不写进仓库文档
5. **中文代码支持**：保留现有中文注释与命名习惯；新代码用英文标识符
6. **严禁破坏开发数据库（P0 红线）**：验证/调试一律用 pytest 隔离库或只读检查。禁止在 `manage.py shell -c` 中对开发库执行任何写操作（create/update/delete）、禁止用开发服务器跑测试/演示脚本（曾因在开发库造数据导致工单编号跳号、误删用户配置的 SMTP/GitCode 凭据，属 P0 事故）。确需操作真实数据时先备份 `db.sqlite3` 且向用户确认

## 常见陷阱速查

| 现象 | 原因 | 处理 |
|------|------|------|
| 凭据/配置页 `InvalidSignature` | SECRET_KEY 被改 | 恢复原 SECRET_KEY 或密文迁移 |
| `get_connection(backend) is not supported with MAILERS` | 走了 Django 邮件入口 | 直接实例化 EmailBackend |
| 邮件发送 `Connection unexpectedly closed` | 465 端口用了 STARTTLS | 465 用 SSL（use_ssl=True），587/25 用 TLS |
| 邮箱更换后邮箱没变 | 前端 `form.submit()` 不带按钮 name | 提交前手动追加 hidden `name="save_profile"` |
| 目标机脚本报 `$'\r'` 错误 | 脚本被转成 CRLF | 用 LF 行尾（`.gitattributes` 已锁定 *.sh） |
| 设备信息查不到 CPU/内存 | 目标机不可达或系统命令缺失 | 检查 SSH 与 `host_info.sh` 输出 |
| HTML 按钮无效 | 嵌套 `<form>` | 拆出独立表单 |
| 登录被锁 | axes 15 分钟 5 次失败 | 清 `AccessAttempt` 或等冷却 |
| 迁移交互式提问 EOFError | 非空字段无默认值 | 迁移加 `default` |
| 申请被拦跳个人中心 | GitCode 用户未设姓名 | 先设置个人资料姓名 |
| 验证码被二次校验拦截 | AJAX 预检消耗了验证码 | 预检用 `consume=False`，保存时真正消耗 |
