# 通知配置

通知能力位于 `notifications` 应用，包含 **邮件（SMTP）** 与 **Webhook** 两种渠道，均在 Django 后台（`/admin/`）配置。

## 邮件（EmailConfig）

| 字段 | 说明 |
|------|------|
| SMTP 服务器 / 端口 | 如 `smtp.example.com:465` |
| 用户名 | SMTP 登录账号 |
| 密码/授权码 | 加密落库 |
| 发件人地址 | 留空则使用用户名 |
| 使用 SSL/TLS | 勾选后使用 TLS 连接 |
| 启用邮件通知 | 开关 |

系统会发送的邮件：

| 场景 | 收件人 | 内容 |
|------|--------|------|
| 新申请提交 | 所有管理员 | 申请详情，提示审批 |
| 审批通过/驳回 | 申请者 | 审批结果与意见 |
| 账号开通成功 | 申请者 | 用户名 + 随机密码 + 到期时间 |

> 注意：启用前请先验证 SMTP 配置可用；发送失败不影响主流程（仅记录日志）。

## Webhook（WebhookConfig）

| 字段 | 说明 |
|------|------|
| 名称 | 便于识别 |
| Webhook URL | 接收 JSON POST 的地址 |
| 密钥 | 可选，会放在 `X-NRM-Signature` 请求头 |
| 启用 | 开关 |

事件格式（统一结构）：

```json
{
  "event": "application.created",
  "timestamp": null,
  "payload": {
    "id": 1,
    "title": "...",
    "username": "...",
    "status": "pending",
    "target_server": { "id": 3, "name": "web-01" }
  }
}
```

支持的事件：

| 事件 | 触发时机 |
|------|----------|
| `application.created` | 新申请提交 |
| `application.reviewed` | 审批完成（payload 含审批意见/时间） |

## 建议

- Webhook 推送超时 5 秒，失败仅记日志，不影响主流程
- 邮件与 Webhook 可同时启用，互不影响
