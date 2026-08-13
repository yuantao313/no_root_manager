# 运维手册

## 定时任务（cron）

### 撤销当日到期的 sudo 权限

```cron
0 1 * * * cd /path/to/project && uv run python manage.py expire_sudo
```

说明：sudo 申请"当天有效"，该命令将已过当日 23:59:59 的 SudoGrant
标记为失效并撤销机器上的 sudo/wheel 组成员资格。

### 到期账号提醒（可选）

账号到期由机器侧 `usermod -e` 控制（到期自动失效），如需到期前提醒，
可扩展管理命令或接入外部 cron 扫描 `valid_until`。

## 目标机器侧规范

### 接管用户

所有受管用户统一加入 `nrm_managed` 组（系统自动创建该组）：

```bash
getent group nrm_managed        # 查看成员
```

### 删除用户（管理员在机器侧手动操作）

```bash
sudo userdel -r <username>                      # 删除用户及 home
sudo gpasswd -d <username> nrm_managed          # 若仍在组中则移出
```

## 邮件配置验证

1. 后台新增 `EmailConfig` 并勾选启用
2. 提交一条测试申请，观察管理员是否收到邮件
3. 若未收到：查看日志（发送失败会记录 `邮件发送失败`），检查 SMTP 端口/认证/TLS

## 常见问题

### 开通失败："创建用户失败：连接失败"

- 服务器凭据不可达，或凭据用户无 sudo 免密权限
- 检查：服务器详情页"测试连接"；确认管理用户在 sudo 组且有 NOPASSWD

### 迁移目录失败："目标目录已存在且非空"

- 用户已存在同名 home 且非空，系统拒绝覆盖
- 人工确认后手动处理，或清空目标后重新申请迁移

### 用户无法登录机器

- 首次登录需修改随机密码（`chage -d 0` 强制）
- 密码丢失无法重发（不落库），需管理员在机器侧重置：`sudo passwd <username>`

## 备份

```bash
cp db.sqlite3 backups/nrm-$(date +%F).sqlite3
```

数据库包含服务器/凭据（加密）/申请/审计记录，建议每日备份。
