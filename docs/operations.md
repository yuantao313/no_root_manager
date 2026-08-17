# 运维手册

## 日常检查

```bash
NRM_ENV=prod uv run python manage.py check --deploy
uv run python manage.py showmigrations
```

同时检查应用日志中的 `SSH`、`开通异常`、`Webhook 推送失败` 和 `邮件发送失败`。通知失败不会回滚工单；SSH 失败会写入工单的“开通结果”。

## 开通失败恢复

1. 在工单详情读取失败原因。
2. 到服务器详情测试连接，核对凭据、sudo 免密权限和 SSH 主机指纹。
3. 修复目标机后，在工单详情点击“重试开通”。
4. 若目标机操作已经部分完成，脚本会复用已有用户并补齐受管组；仍应人工核对目标机状态。

## 目标机要求

- 管理账号为 root，或具备 `sudo -n` 免密提权能力。
- SSH 主机指纹必须通过可信渠道核验后录入。
- 初始化操作会创建 `nrm_managed` 组和 `/etc/motd.d` 相关目录。
- NRM 不负责操作系统补丁、监控、资源配额或账号自动到期。

## SQLite 备份与恢复

不要在服务写入期间直接复制数据库文件。推荐停服务后复制，或使用 SQLite 在线备份命令：

```bash
mkdir -p backups
sqlite3 db.sqlite3 ".backup 'backups/nrm.sqlite3'"
```

同时备份生产环境配置中的 `NRM_SECRET_KEY`。该密钥用于解密凭据和通知秘密；丢失或随意轮换后，历史密文无法恢复。

恢复前停止服务，备份当前数据库，再替换数据库文件并执行：

```bash
uv run python manage.py migrate
uv run python manage.py check
```

## 常见问题

| 现象 | 排查 |
|------|------|
| `database is locked` | 确认只运行一个 Gunicorn worker；缩短外部脚本之外的数据库事务；检查磁盘与备份任务 |
| SSH 指纹不匹配 | 停止操作，通过可信渠道确认目标机是否更换；不要直接接受新指纹 |
| `InvalidSignature` | 恢复原 `NRM_SECRET_KEY`，或对加密字段执行明确的数据迁移 |
| 465 端口邮件断开 | 465 使用 SSL；587/25 使用 STARTTLS |
| 登录被锁定 | 等待 15 分钟冷却；紧急情况下由运维人员使用 axes 管理命令处理 |
