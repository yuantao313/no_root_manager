"""通知服务：邮件 + Webhook 发送审批相关通知。"""

import json
import logging
import threading
import urllib.request
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend
from django.db.models import Q
from django.urls import reverse

from .models import EmailConfig, WebhookConfig
from .security import UnsafeWebhookURL, open_webhook_request, validate_webhook_url

logger = logging.getLogger(__name__)
_WEBHOOK_PLATFORMS = {value for value, _label in WebhookConfig.PLATFORM_CHOICES}


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

    # pytest 的事务隔离不允许后台线程跨用例访问测试库；同步执行也让断言确定。
    if settings.DJANGO_TESTING:
        func(*args)
        return

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
        alias="nrm-database-smtp",
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
    )
    try:
        sent = connection.send_messages([message])
        if not sent:
            logger.error("邮件后端未发送任何消息：%s -> %s", subject, to_list)
            return False
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
    cfg = EmailConfig.get_current()
    if not cfg or not cfg.enabled or not to_list:
        logger.info("邮件未发送（未启用/未配置）：%s -> %s", subject, to_list)
        return False
    if cfg.send_via == EmailConfig.SEND_VIA_WEBHOOK:
        return _send_email_via_webhook(cfg, subject, body, to_list)
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


def _send_email_via_webhook(cfg, subject: str, body: str, to_list: list[str]) -> bool:
    """通过邮件 Webhook 发送邮件（规避 SMTP 端口屏蔽）。

    请求格式：POST <mail_webhook_url>
      headers: Content-Type: application/json
               X-Webhook-Token: <mail_webhook_token>
      body:    {"to": "a@x.com,b@x.com", "subject": "...", "body": "..."}
    to 为逗号分隔的收件人字符串（对齐外部端点格式，多收件人用逗号拼接）。
    仅当 send_via=webhook 且配置了 URL 时使用；失败不影响主流程（返回 False）。
    """
    url = (cfg.mail_webhook_url or "").strip()
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
        with open_webhook_request(req, timeout=10) as resp:
            if not 200 <= resp.status < 300:
                logger.error("邮件 Webhook 返回非成功状态：%s", resp.status)
                return False
            logger.info("邮件已发送（Webhook）：%s -> %s (%s)", subject, to_list, resp.status)
            return True
    except Exception:  # noqa: BLE001 —— 邮件失败不应影响主流程
        logger.exception("邮件发送失败（Webhook）：%s -> %s", subject, to_list)
        return False


def _application_reviewers(application=None):
    """返回活跃管理员；传入工单时按服务器审批权限收窄。"""
    User = get_user_model()
    users = User.objects.filter(is_staff=True, is_active=True)
    if application is None:
        return users
    scope = Q(is_superuser=True)
    if application.target_server_id:
        scope |= Q(server_bindings__server_id=application.target_server_id)
    return users.filter(scope).distinct()


def admin_emails(application=None) -> list[str]:
    """返回有权处理该工单的管理员邮箱；未传工单时保留通用查询。"""
    return list(_application_reviewers(application).exclude(email="").values_list("email", flat=True))


def _application_detail_lines(payload: dict) -> list[str]:
    """把邮件与飞书共用的申请字段格式化为可读文本行。"""
    lines = []
    name = payload.get("applicant_name") or ""
    username = payload.get("username") or ""
    if name or username:
        lines.append(f"申请人：{name}（{username}）")
    fields = (
        ("工号", payload.get("employee_id")),
        ("类型", payload.get("apply_type_display")),
        ("目标服务器", (payload.get("target_server") or {}).get("name")),
        ("申请内容", payload.get("description")),
        ("状态", payload.get("status_display") or payload.get("status")),
        ("审批意见", payload.get("review_comment")),
        ("开通结果", payload.get("provision_note")),
    )
    lines.extend(f"{label}：{value}" for label, value in fields if value)
    return lines


def _format_mail_details(application) -> str:
    """生成邮件正文的完整申请详情（与 webhook 信息一致：工单号/申请人/工号/类型/服务器/内容/状态/审批意见/链接）。

    供 notify_new_application / notify_review_result 复用，避免邮件只有标题没有详情。
    """
    payload = _application_payload(application)
    payload["review_comment"] = application.review_comment
    lines = [f"工单 #{application.pk}", *_application_detail_lines(payload)]
    link = _review_link(payload)
    if link:
        lines.append(f"工单链接：{link}")
    return "\n".join(lines)


def notify_new_application(application) -> bool:
    """新申请提交时通知管理员（邮件正文包含完整申请详情）。"""
    subject = f"[NRM] 新申请待审批：{application.title or application.description[:30]}"
    body = f"收到新的申请：\n\n{_format_mail_details(application)}\n\n请登录系统及时审批。"
    return send_email(subject, body, admin_emails(application))


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


def notify_application(application, reviewed=False):
    """按事件阶段编排邮件与 Webhook，保持既有发送顺序。"""
    handlers = (
        (notify_review_result, webhook_review_result) if reviewed else (webhook_new_application, notify_new_application)
    )
    for handler in handlers:
        handler(application)


def send_provision_credentials(application, password) -> bool:
    """开通成功后将随机密码发送给申请者。

    与工单同步通知：初始密码已写入申请工单（加密存储），
    即使邮件未开启/未送达，用户也可登录 NRM 在工单详情查看。
    """
    if not application.email or not password:
        return False
    subject = f"[NRM] 您的服务器账号已开通：{application.username}"
    body = (
        f"您好，{application.applicant_name}：\n\n"
        f"您在 {application.target_server} 上的账号已开通。\n\n"
        f"用户名：{application.username}\n"
        f"初始密码：{password}\n"
        f"服务器：{application.target_server}\n\n"
        f"【重要】请先使用系统终端通过 SSH 登录服务器，并按提示修改初始密码。\n"
        f"完成修改前，请勿使用 VS Code Remote SSH 等工具登录；密码修改成功后再使用这些工具。\n\n"
        f"如未收到本邮件，可登录 NRM 系统，在「我的申请 → 申请详情」中查看初始密码。"
    )
    return send_email(subject, body, [application.email])


def send_machine_password_reset(user, server, username, password) -> bool:
    """将目标机器账号的新临时密码发送给绑定的平台用户。"""
    if not user or not user.email or not username or not password:
        return False
    display_name = user.get_full_name().strip() or user.username
    subject = f"[NRM] 您的服务器账号密码已重置：{username}"
    body = (
        f"您好，{display_name}：\n\n"
        f"管理员已重置您在 {server} 上的机器账号密码。\n\n"
        f"机器用户名：{username}\n"
        f"临时密码：{password}\n"
        f"服务器：{server}\n\n"
        f"【重要】请先使用系统终端通过 SSH 登录服务器，并按提示修改临时密码。\n"
        f"完成修改前，请勿使用 VS Code Remote SSH 等工具登录；密码修改成功后再使用这些工具。\n\n"
        f"若您未申请此次重置，请及时联系管理员。"
    )
    return send_email(subject, body, [user.email])


# ------------------------- Webhook -------------------------


def _is_feishu_url(url: str) -> bool:
    """是否为飞书/Lark 机器人 Webhook 域名（无平台配置时按 URL 兜底判断）。"""
    hostname = (urlsplit(url).hostname or "").rstrip(".").lower()
    return hostname in {"open.feishu.cn", "open.larksuite.com"}


def _webhook_destination(url: str) -> str:
    """日志只记录目标主机，不泄漏通常包含机器人密钥的完整路径。"""
    parsed = urlsplit(url)
    return parsed.hostname or "unknown-host"


def _site_base_url() -> str:
    """站点基准地址（供审批链接使用）。

    优先取系统设置中的 site_base_url（数据库配置），
    留空时回退 settings.GITCODE_CALLBACK_BASE_URL；均未配置返回空串。
    """
    try:
        from accounts.models import SystemConfig

        config = SystemConfig.objects.first()
        if config:
            return config.get_site_base_url()
        return settings.GITCODE_CALLBACK_BASE_URL.strip().rstrip("/")
    except Exception:  # noqa: BLE001 —— 取不到站点地址时链接留空
        return ""


def _review_link(payload: dict) -> str:
    """构造申请详情页链接（管理员点击直达审批）。"""
    app_id = payload.get("id")
    if not app_id:
        return ""
    return f"{_site_base_url()}{reverse('applications:detail', args=[app_id])}"


def _format_feishu_text(event: str, payload: dict) -> str:
    """把事件 payload 解析成飞书可读文本（不再直接展示原始 JSON）。"""
    lines = [f"[NRM] {event}"]
    app_id = payload.get("id")
    if app_id:
        lines.append(f"工单 #{app_id}")
    lines.extend(_application_detail_lines(payload))
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
    try:
        url = validate_webhook_url(url)
    except UnsafeWebhookURL as exc:
        return False, str(exc)

    body = _build_webhook_body(
        platform, url, event, payload if payload is not None else {"message": "NRM Webhook 连通性测试"}
    )
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
        with open_webhook_request(req, timeout=5) as resp:
            if not 200 <= resp.status < 300:
                logger.error(
                    "Webhook HTTP 失败：%s -> %s (%s)",
                    event,
                    _webhook_destination(url),
                    resp.status,
                )
                return False, f"推送失败：HTTP {resp.status}"
            resp_body = resp.read().decode("utf-8", errors="replace").strip()
            # 尝试解析响应体业务码（飞书: {"code":0,"msg":"success"}）
            try:
                data = json.loads(resp_body) if resp_body else {}
                code = data.get("code")
                if code is not None and int(code) != 0:
                    msg = data.get("msg") or resp_body or f"业务码 {code}"
                    logger.error("Webhook 业务失败：%s -> %s (%s)", event, _webhook_destination(url), msg)
                    return False, f"推送失败：{msg}"
            except (ValueError, TypeError):
                pass  # 非 JSON 响应体，按 HTTP 状态判断
            logger.info("Webhook 推送成功：%s -> %s (%s)", event, _webhook_destination(url), resp.status)
            return True, f"推送成功（HTTP {resp.status}）"
    except Exception as e:  # noqa: BLE001 —— 需兜底展示失败原因
        logger.exception("Webhook 推送失败：%s -> %s", event, _webhook_destination(url))
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


def send_webhook(event: str, payload: dict, *, application=None) -> bool:
    """向全局及有权处理该工单的个人 Webhook 推送事件。"""
    hooks = WebhookConfig.objects.filter(enabled=True)
    if application is None:
        # 没有权限上下文的通用事件只能推送到超级管理员维护的全局 Hook。
        hooks = hooks.filter(owner__isnull=True)
    else:
        hooks = hooks.filter(Q(owner__isnull=True) | Q(owner__in=_application_reviewers(application)))
    if not hooks:
        return False
    ok = True
    for hook in hooks:
        platform = hook.name if hook.name in _WEBHOOK_PLATFORMS else WebhookConfig.PLATFORM_GENERIC
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
        "status": application.status,
        "status_display": application.get_status_display(),
        "description": application.description,
        "provision_note": application.provision_note,
    }


def webhook_new_application(application) -> bool:
    """新申请事件推送 Webhook。"""
    return send_webhook("application.created", _application_payload(application), application=application)


def webhook_review_result(application) -> bool:
    """审批结果事件推送 Webhook。"""
    payload = _application_payload(application)
    payload.update(
        {
            "review_comment": application.review_comment,
            "reviewed_at": application.reviewed_at.isoformat() if application.reviewed_at else None,
        }
    )
    return send_webhook("application.reviewed", payload, application=application)
