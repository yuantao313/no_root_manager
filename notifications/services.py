"""通知服务：邮件 + Webhook 发送审批相关通知。"""

import json
import logging
import urllib.request

from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend

from .models import EmailConfig, WebhookConfig

logger = logging.getLogger(__name__)


def send_email_with_config(
    host, port, username, password, from_email, use_ssl, subject: str, body: str, to_list: list[str]
) -> bool:
    """使用指定 SMTP 配置发送邮件（不依赖数据库 EmailConfig）。

    用于 SMTP 配置在写入数据库前验证可用性（发验证码邮件）。

    use_ssl=True 为 SSL 直连（465 端口）；False 为 STARTTLS（587/25）。
    """
    from_email = from_email or username
    connection = EmailBackend(
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=not use_ssl,
        use_ssl=use_ssl,
        fail_silently=False,
    )
    message = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=to_list,
        connection=connection,
    )
    try:
        message.send()
        logger.info("邮件已发送（指定配置）：%s -> %s", subject, to_list)
        return True
    except Exception:  # noqa: BLE001 —— 邮件失败不应影响主流程
        logger.exception("邮件发送失败（指定配置）：%s -> %s", subject, to_list)
        return False


def send_email(subject: str, body: str, to_list: list[str]) -> bool:
    """使用 EmailConfig 配置发送邮件。未启用或未配置时返回 False。"""
    cfg = EmailConfig.objects.first()
    if not cfg or not cfg.enabled or not cfg.host or not to_list:
        logger.info("邮件未发送（未启用/未配置）：%s -> %s", subject, to_list)
        return False
    return send_email_with_config(
        cfg.host,
        cfg.port,
        cfg.username,
        cfg.password,
        cfg.from_email,
        cfg.use_ssl,
        subject,
        body,
        to_list,
    )


def admin_emails() -> list[str]:
    """所有启用邮箱的管理员地址。"""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return list(User.objects.filter(is_staff=True).exclude(email="").values_list("email", flat=True))


def notify_new_application(application) -> bool:
    """新申请提交时通知管理员。"""
    subject = f"[NRM] 新申请待审批：{application.title}"
    body = (
        f"收到新的申请：\n\n"
        f"标题：{application.title}\n"
        f"申请人：{application.applicant_name}（{application.username} / {application.email}）\n"
        f"工号：{application.employee_id or '-'}\n"
        f"类型：{application.get_apply_type_display()}\n"
        f"目标服务器：{application.target_server or '-'}\n"
        f"申请内容：{application.description}\n\n"
        f"请登录系统及时审批。"
    )
    return send_email(subject, body, admin_emails())


def notify_review_result(application) -> bool:
    """审批结果通知申请者。"""
    if not application.email:
        return False
    status = application.get_status_display()
    subject = f"[NRM] 您的申请已{status}：{application.title}"
    reviewed_at = application.reviewed_at.strftime("%Y-%m-%d %H:%M") if application.reviewed_at else "-"
    body = (
        f"您好，{application.applicant_name}：\n\n"
        f"您的申请《{application.title}》已被{status}。\n"
        f"审批意见：{application.review_comment or '（无）'}\n"
        f"审批时间：{reviewed_at}\n\n"
        f"如有疑问请联系管理员。"
    )
    return send_email(subject, body, [application.email])


def send_provision_credentials(application, password, expire_date=None) -> bool:
    """开通成功后将随机密码（及到期时间）发送给申请者。"""
    if not application.email or not password:
        return False
    subject = f"[NRM] 您的服务器账号已开通：{application.username}"
    expire_text = f"\n账号到期时间：{expire_date}（到期后自动失效）" if expire_date else ""
    body = (
        f"您好，{application.applicant_name}：\n\n"
        f"您在 {application.target_server} 上的账号已开通。\n\n"
        f"用户名：{application.username}\n"
        f"随机密码：{password}\n"
        f"服务器：{application.target_server}{expire_text}\n\n"
        f"请妥善保管密码，首次登录后建议尽快修改。"
    )
    return send_email(subject, body, [application.email])


# ------------------------- Webhook -------------------------


def send_webhook(event: str, payload: dict) -> bool:
    """向所有启用的 Webhook 推送 JSON 事件。"""
    hooks = WebhookConfig.objects.filter(enabled=True)
    if not hooks:
        return False
    ok = True
    for hook in hooks:
        data = {
            "event": event,
            "timestamp": None,  # 由视图层填充或留空
            "payload": payload,
        }
        headers = {"Content-Type": "application/json"}
        if hook.secret:
            headers["X-NRM-Signature"] = hook.secret
        req = urllib.request.Request(
            hook.url,
            data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                logger.info("Webhook 已推送：%s -> %s (%s)", event, hook.url, resp.status)
        except Exception:  # noqa: BLE001 —— webhook 失败不应影响主流程
            logger.exception("Webhook 推送失败：%s -> %s", event, hook.url)
            ok = False
    return ok


def _application_payload(application) -> dict:
    return {
        "id": application.pk,
        "title": application.title,
        "applicant_name": application.applicant_name,
        "username": application.username,
        "email": application.email,
        "employee_id": application.employee_id,
        "apply_type": application.apply_type,
        "apply_type_display": application.get_apply_type_display(),
        "target_server": (
            {"id": application.target_server.pk, "name": application.target_server.name}
            if application.target_server
            else None
        ),
        "status": application.status,
        "description": application.description,
    }


def webhook_new_application(application) -> bool:
    """新申请事件推送 Webhook。"""
    return send_webhook("application.created", _application_payload(application))


def webhook_review_result(application) -> bool:
    """审批结果事件推送 Webhook。"""
    payload = _application_payload(application)
    payload.update(
        {
            "review_comment": application.review_comment,
            "reviewed_at": application.reviewed_at.isoformat() if application.reviewed_at else None,
        }
    )
    return send_webhook("application.reviewed", payload)
