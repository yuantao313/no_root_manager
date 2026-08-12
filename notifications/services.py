"""通知服务：邮件 + Webhook 发送审批相关通知。"""

import json
import logging
import threading
import urllib.request

from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend

from .models import EmailConfig, WebhookConfig

logger = logging.getLogger(__name__)


def run_in_background(func, *args):
    """后台线程执行通知类任务（daemon，进程退出自动终止）。

    通知（邮件/Webhook）是纯 I/O，不该阻塞申请提交/审批的 HTTP 请求。
    线程内会关闭旧 DB 连接（Django 多线程必需），并兜底记录异常。
    """

    def _runner():
        from django.db import close_old_connections

        close_old_connections()
        try:
            func(*args)
        except Exception:  # noqa: BLE001 —— 后台通知失败不影响主流程
            logger.exception("后台通知任务执行失败：%s", getattr(func, "__name__", func))
        finally:
            close_old_connections()

    threading.Thread(target=_runner, daemon=True).start()


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
        # SMTP 连接/读写超时：防止目标服务器不可达时 TCP 连接无限挂起，
        # 拖住申请提交等主流程（表现为页面一直转圈、后续 webhook 不发送）
        timeout=10,
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
    """使用 EmailConfig 配置发送邮件。未启用或未配置时返回 False。

    发送方式由 cfg.send_via 显式决定（smtp / webhook），不自动降级：
    - smtp：原 EmailBackend 直连逻辑（不改动）
    - webhook：POST 到邮件 Webhook（规避 SMTP 端口屏蔽）
    """
    cfg = EmailConfig.objects.first()
    if not cfg or not cfg.enabled or not to_list:
        logger.info("邮件未发送（未启用/未配置）：%s -> %s", subject, to_list)
        return False
    if cfg.send_via == EmailConfig.SEND_VIA_WEBHOOK:
        return send_email_via_webhook(subject, body, to_list)
    if not cfg.host:
        logger.info("邮件未发送（未配置 SMTP）：%s -> %s", subject, to_list)
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


def send_email_via_webhook(subject: str, body: str, to_list: list[str]) -> bool:
    """通过邮件 Webhook 发送邮件（规避 SMTP 端口屏蔽）。

    请求格式：POST <mail_webhook_url>
      headers: Content-Type: application/json
               X-Webhook-Token: <mail_webhook_token>
      body:    {"to": "a@x.com,b@x.com", "subject": "...", "body": "..."}
    to 为逗号分隔的收件人字符串（对齐外部端点格式，多收件人用逗号拼接）。
    仅当 send_via=webhook 且配置了 URL 时使用；失败不影响主流程（返回 False）。
    """
    cfg = EmailConfig.objects.first()
    url = (cfg.mail_webhook_url or "").strip() if cfg else ""
    if not url or not to_list:
        logger.info("邮件 Webhook 未发送（未配置 URL）：%s -> %s", subject, to_list)
        return False
    data = json.dumps(
        {"to": ",".join(to_list), "subject": subject, "body": body},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cfg.mail_webhook_token:
        headers["X-Webhook-Token"] = cfg.mail_webhook_token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("邮件已发送（Webhook）：%s -> %s (%s)", subject, to_list, resp.status)
            return True
    except Exception:  # noqa: BLE001 —— 邮件失败不应影响主流程
        logger.exception("邮件发送失败（Webhook）：%s -> %s", subject, to_list)
        return False


def admin_emails() -> list[str]:
    """所有启用邮箱的管理员地址。"""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return list(User.objects.filter(is_staff=True).exclude(email="").values_list("email", flat=True))


def _format_mail_details(application) -> str:
    """生成邮件正文的完整申请详情（与 webhook 信息一致：工单号/申请人/工号/类型/服务器/内容/状态/审批意见/链接）。

    供 notify_new_application / notify_review_result 复用，避免邮件只有标题没有详情。
    """
    from django.urls import reverse

    lines = [f"工单 #{application.pk}"]
    lines.append(f"申请人：{application.applicant_name}（{application.username}）")
    if application.employee_id:
        lines.append(f"工号：{application.employee_id}")
    lines.append(f"类型：{application.get_apply_type_display()}")
    if application.target_server:
        lines.append(f"目标服务器：{application.target_server.name}")
    # NPU 机器申请：展示用户申请的 NPU 卡组（过滤公共组 npu，不暴露"用户组"概念）
    groups = application.npu_groups_display()
    if application.target_server and application.target_server.is_npu and groups:
        lines.append(f"申请 NPU 卡组：{groups}")
    if application.description:
        lines.append(f"申请内容：{application.description}")
    lines.append(f"状态：{application.get_status_display()}")
    if application.review_comment:
        lines.append(f"审批意见：{application.review_comment}")
    link = f"{_site_base_url()}{reverse('applications:detail', args=[application.pk])}"
    if link:
        lines.append(f"工单链接：{link}")
    return "\n".join(lines)


def notify_new_application(application) -> bool:
    """新申请提交时通知管理员（邮件正文包含完整申请详情）。"""
    subject = f"[NRM] 新申请待审批：{application.title or application.description[:30]}"
    body = (
        f"收到新的申请：\n\n"
        f"{_format_mail_details(application)}\n\n"
        f"请登录系统及时审批。"
    )
    return send_email(subject, body, admin_emails())


def notify_review_result(application) -> bool:
    """审批结果通知申请者（邮件正文包含完整申请详情）。"""
    if not application.email:
        return False
    status = application.get_status_display()
    subject = f"[NRM] 您的申请已{status}：{application.title or application.description[:30]}"
    reviewed_at = application.reviewed_at.strftime("%Y-%m-%d %H:%M") if application.reviewed_at else "-"
    body = (
        f"您好，{application.applicant_name}：\n\n"
        f"您的申请已{status}。\n"
        f"审批时间：{reviewed_at}\n\n"
        f"{_format_mail_details(application)}\n\n"
        f"如有疑问请联系管理员。"
    )
    return send_email(subject, body, [application.email])


def send_provision_credentials(application, password, expire_date=None) -> bool:
    """开通成功后将随机密码（及到期时间）发送给申请者。

    与工单同步通知：初始密码已写入申请工单（加密存储），
    即使邮件未开启/未送达，用户也可登录 NRM 在工单详情查看。
    """
    if not application.email or not password:
        return False
    subject = f"[NRM] 您的服务器账号已开通：{application.username}"
    expire_text = f"\n账号到期时间：{expire_date}（到期后自动失效）" if expire_date else ""
    body = (
        f"您好，{application.applicant_name}：\n\n"
        f"您在 {application.target_server} 上的账号已开通。\n\n"
        f"用户名：{application.username}\n"
        f"初始密码：{password}\n"
        f"服务器：{application.target_server}{expire_text}\n\n"
        f"【重要】首次登录必须修改密码（服务器已强制设置）。\n\n"
        f"如未收到本邮件，可登录 NRM 系统，在「我的申请 → 申请详情」中查看初始密码。"
    )
    return send_email(subject, body, [application.email])


# ------------------------- Webhook -------------------------


def _is_feishu_url(url: str) -> bool:
    """是否为飞书/Lark 机器人 Webhook 域名（无平台配置时按 URL 兜底判断）。"""
    return "open.feishu.cn" in url or "open.larksuite.com" in url


def _site_base_url() -> str:
    """站点基准地址（供审批链接使用）。

    优先取系统设置中的 site_base_url（数据库配置），
    留空时回退 settings.GITCODE_CALLBACK_BASE_URL；均未配置返回空串。
    """
    try:
        from accounts.models import SystemConfig

        return SystemConfig.get_singleton().get_site_base_url()
    except Exception:  # noqa: BLE001 —— 取不到站点地址时链接留空
        return ""


def _review_link(payload: dict) -> str:
    """构造申请详情页链接（管理员点击直达审批）。"""
    app_id = payload.get("id")
    if not app_id:
        return ""
    from django.urls import reverse

    return f"{_site_base_url()}{reverse('applications:detail', args=[app_id])}"


def _format_feishu_text(event: str, payload: dict) -> str:
    """把事件 payload 解析成飞书可读文本（不再直接展示原始 JSON）。"""
    lines = [f"[NRM] {event}"]
    app_id = payload.get("id")
    if app_id:
        lines.append(f"工单 #{app_id}")
    name = payload.get("applicant_name") or ""
    username = payload.get("username") or ""
    if name or username:
        lines.append(f"申请人：{name}（{username}）")
    if payload.get("employee_id"):
        lines.append(f"工号：{payload['employee_id']}")
    if payload.get("apply_type_display"):
        lines.append(f"类型：{payload['apply_type_display']}")
    server = payload.get("target_server") or {}
    if server.get("name"):
        lines.append(f"目标服务器：{server['name']}")
    # NPU 机器申请：提示管理员用户申请的 NPU 卡组（过滤公共组 npu，不暴露"用户组"概念）
    if payload.get("target_server_is_npu") and payload.get("applied_groups"):
        groups = [g.strip() for g in payload["applied_groups"].split(",") if g.strip() and g.strip() != "npu"]
        if groups:
            lines.append(f"申请 NPU 卡组：{','.join(groups)}")
    if payload.get("description"):
        lines.append(f"申请内容：{payload['description']}")
    if payload.get("status"):
        lines.append(f"状态：{payload['status']}")
    if payload.get("review_comment"):
        lines.append(f"审批意见：{payload['review_comment']}")
    link = _review_link(payload)
    if link:
        lines.append(f"审批链接：{link}")
    return "\n".join(lines)


def _build_webhook_body(platform: str, url: str, event: str, payload: dict) -> str:
    """按平台组装请求体。

    - feishu/Lark 机器人：可读文本消息（parse payload 后展示，含审批链接）
    - 其他平台（generic）：NRM 通用事件格式（原始 JSON）
    """
    if platform == WebhookConfig.PLATFORM_FEISHU or _is_feishu_url(url):
        text = _format_feishu_text(event, payload)
        return json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False)
    return json.dumps(
        {
            "event": event,
            "timestamp": None,
            "payload": payload,
        },
        ensure_ascii=False,
    )


def _post_webhook(
    url: str, secret: str, event: str, payload: dict | None, platform: str = WebhookConfig.PLATFORM_GENERIC
) -> tuple[bool, str]:
    """向指定 URL 推送一条 JSON 事件，解析响应体业务码。

    部分平台（飞书等）业务失败时仍返回 HTTP 200，仅在响应体 code 字段
    表示错误——只认 HTTP 状态会误报成功。返回 (是否成功, 提示信息)。
    """
    body = _build_webhook_body(platform, url, event, payload if payload is not None else {"message": "NRM Webhook 连通性测试"})
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-NRM-Signature"] = secret
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace").strip()
            # 尝试解析响应体业务码（飞书: {"code":0,"msg":"success"}）
            try:
                data = json.loads(resp_body) if resp_body else {}
                code = data.get("code")
                if code is not None and int(code) != 0:
                    msg = data.get("msg") or resp_body or f"业务码 {code}"
                    logger.error("Webhook 业务失败：%s -> %s (%s)", event, url, msg)
                    return False, f"推送失败：{msg}"
            except (ValueError, TypeError):
                pass  # 非 JSON 响应体，按 HTTP 状态判断
            logger.info("Webhook 推送成功：%s -> %s (%s)", event, url, resp.status)
            return True, f"推送成功（HTTP {resp.status}）"
    except Exception as e:  # noqa: BLE001 —— 需兜底展示失败原因
        logger.exception("Webhook 推送失败：%s -> %s", event, url)
        return False, f"推送失败：{e}"


def send_webhook_to(
    url: str, secret: str, event: str = "test", payload: dict | None = None, platform: str = ""
) -> tuple[bool, str]:
    """向指定 URL 推送一条 JSON 事件（用于保存前测试连通性）。

    与 send_webhook 不同：不查数据库，直接对给定 URL 发送，
    返回 (是否成功, 提示信息)。payload 默认发送一条测试消息。
    platform 为空时按 URL 自动判断（飞书域名走飞书格式）。
    """
    url = (url or "").strip()
    if not url:
        return False, "Webhook URL 为空"
    platform = platform or (WebhookConfig.PLATFORM_FEISHU if _is_feishu_url(url) else WebhookConfig.PLATFORM_GENERIC)
    return _post_webhook(url, secret, event, payload, platform)


def send_webhook(event: str, payload: dict) -> bool:
    """向所有启用的 Webhook 推送 JSON 事件（按各自配置的平台格式化）。"""
    hooks = WebhookConfig.objects.filter(enabled=True)
    if not hooks:
        return False
    ok = True
    for hook in hooks:
        platform = hook.name if hook.name in dict(WebhookConfig.PLATFORM_CHOICES) else WebhookConfig.PLATFORM_GENERIC
        ok_send, _ = _post_webhook(hook.url, hook.secret, event, payload, platform)
        if not ok_send:
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
        "target_server_is_npu": bool(application.target_server and application.target_server.is_npu),
        "applied_groups": application.applied_groups or "",
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
