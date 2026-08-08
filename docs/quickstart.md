# 快速开始

## 环境要求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)（依赖管理）
- 一台或多台可通过 SSH 管理的目标服务器（需有 sudo 免密权限的管理账户）

## 安装与启动

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

访问 http://127.0.0.1:8000/。

## 首个流程（最小闭环）

1. **后台配置凭据**：`/credentials/new/` 添加目标机器的管理凭据（用户名 + 密码或私钥）
2. **后台添加服务器**：`/servers/new/` 填写地址/端口，选择凭据，保存并测试连接
3. **用户提交申请**：访问 `/applications/new/`（无需登录），填写身份信息并选择目标服务器
4. **管理员审批**：登录后台 `/accounts/login/`，在申请列表中点"通过"
5. **自动开通**：系统在目标机器创建用户、生成随机密码并邮件发送；申请者可用该密码登录机器（首次登录强制改密）

## 常用命令

```bash
uv run python manage.py check              # Django 系统检查
uv run pytest                              # 单元测试
uv run python manage.py expire_sudo        # 撤销当日到期的 sudo 权限（建议 cron 每天执行）
uv run mkdocs serve                        # 本地预览文档
```

## 定时任务建议

```cron
# 每天凌晨撤销昨日到期的 sudo 权限
0 1 * * * cd /path/to/project && uv run python manage.py expire_sudo
```
