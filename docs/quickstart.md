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

1. **添加服务器**（超级管理员）：`/servers/new/` 填写地址/端口，关联管理凭据，保存并测试连接
2. **用户提交申请**（普通用户）：登录后进入「我的申请」，选择"申请服务器账号"并选择目标服务器，提交
3. **管理员审批**：在「申请列表」中点击"通过"
4. **自动开通**：系统在目标机器创建用户、生成随机密码并邮件发送；申请者可用该密码登录机器（首次登录强制改密）

详细操作见[使用指南](usage/index.md)，完整部署见[部署指南](deploy.md)。

## 常用命令

```bash
uv run python manage.py check              # Django 系统检查
uv run pytest                              # 单元测试
uv run mkdocs serve                        # 本地预览文档
```
