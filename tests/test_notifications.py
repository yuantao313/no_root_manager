"""通知服务测试：邮件/Webhook 的配置、降级与事件 payload。"""

from unittest.mock import patch

import pytest
from django.test import override_settings

from accounts.models import SystemConfig
from applications.models import Application
from notifications.models import EmailConfig, WebhookConfig
from notifications.services import (
    admin_emails,
    notify_application,
    notify_new_application,
    notify_review_result,
    send_email,
    send_webhook,
    webhook_new_application,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def application():
    return Application.objects.create(
        applicant_name="张三",
        username="zs",
        email="zs@example.com",
        employee_id="E1",
        title="通知测试",
    )


class TestSendEmailWebhook:
    """邮件 Webhook：send_via=webhook 时走独立端点（规避 SMTP 端口屏蔽）。"""

    def _cfg(self, **kw):
        defaults = dict(
            host="smtp.example.com",
            port=465,
            username="u",
            enabled=True,
            send_via=EmailConfig.SEND_VIA_WEBHOOK,
            mail_webhook_url="https://hooks.example.com/webhook/mail",
            mail_webhook_token="secret-token",
        )
        defaults.update(kw)
        return EmailConfig.objects.create(**defaults)

    def test_webhook_mode_posts_json_with_token(self, application, django_assert_num_queries):
        """send_via=webhook：POST {to,subject,body} 且带 X-Webhook-Token 头。"""
        from notifications.services import send_email

        self._cfg()
        with patch("notifications.services.open_webhook_request") as mock:
            mock.return_value.__enter__.return_value.status = 200
            with django_assert_num_queries(1):
                ok = send_email("主题", "正文", ["a@b.com", "c@d.com"])
        assert ok is True
        req = mock.call_args.args[0]
        # urllib 会把 header 名规范化为 X-webhook-token（HTTP 头大小写不敏感）
        headers = {k.lower(): v for k, v in req.header_items()}
        assert headers.get("x-webhook-token") == "secret-token"
        assert headers.get("content-type") == "application/json"
        import json as _json

        payload = _json.loads(req.data)
        assert payload["to"] == "a@b.com,c@d.com"  # 逗号分隔收件人字符串（对齐外部端点）
        assert payload["subject"] == "主题"
        assert payload["body"] == "正文"

    def test_webhook_mode_failure_returns_false(self, application):
        from notifications.services import send_email

        self._cfg()
        with patch("notifications.services.open_webhook_request", side_effect=Exception("net down")):
            assert send_email("t", "b", ["a@b.com"]) is False

    def test_webhook_mode_without_url_returns_false(self, application):
        from notifications.services import send_email

        self._cfg(mail_webhook_url="")
        assert send_email("t", "b", ["a@b.com"]) is False

    def test_smtp_mode_still_uses_email_backend(self, application):
        """send_via=smtp（默认）：行为不变，仍走原 SMTP 直连逻辑。"""
        from notifications.services import send_email

        self._cfg(send_via=EmailConfig.SEND_VIA_SMTP)
        with patch("notifications.services.EmailBackend") as mock_backend:
            mock_backend.return_value.send_messages.return_value = 1
            ok = send_email("t", "b", ["a@b.com"])
        assert ok is True
        assert mock_backend.call_count == 1

    def test_settings_page_renders_mail_webhook_tab(self):
        """设置页渲染邮件 Webhook：已整合进"邮件通知"tab（发送方式单选），无独立 tab。"""
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse

        su = get_user_model().objects.create_user(
            username="admin", password="x12345!", is_staff=True, is_superuser=True
        )
        c = Client()
        c.force_login(su)
        html = c.get(reverse("accounts:settings")).content.decode()
        assert 'id="tab-mail-webhook"' not in html  # 无独立邮件 Webhook tab
        assert 'name="send_via" value="webhook"' in html  # 发送方式单选（含 webhook）
        assert 'id="webhook-settings"' in html  # webhook 设置区
        assert 'id="smtp-settings"' in html  # smtp 设置区
        assert 'data-switch="email"' in html  # 通知总开关保留

    def test_no_config_returns_false(self, application):
        EmailConfig.objects.all().delete()
        assert send_email("t", "b", ["a@b.com"]) is False

    def test_enabled_sends_via_configured_backend(self, application):
        EmailConfig.objects.create(
            host="smtp.example.com",
            port=587,
            username="nrm",
            password="pw",
            from_email="nrm@x.com",
            use_ssl=True,
            enabled=True,
        )
        with patch("notifications.services.EmailBackend") as mock_backend:
            mock_backend.return_value.send_messages.return_value = 1
            send_email("主题", "内容", ["a@b.com"])
            # 使用配置的 host/port，而非默认 localhost
            assert mock_backend.call_args.kwargs["host"] == "smtp.example.com"
            assert mock_backend.call_args.kwargs["port"] == 587

    def test_enabled_config_is_preferred_over_older_disabled_record(self):
        EmailConfig.objects.create(host="old.example.com", username="old", enabled=False)
        current = EmailConfig.objects.create(host="new.example.com", username="new", enabled=True)

        with patch("notifications.services.EmailBackend") as backend:
            backend.return_value.send_messages.return_value = 1
            assert send_email("主题", "内容", ["a@b.com"]) is True

        assert EmailConfig.get_current() == current
        assert backend.call_args.kwargs["host"] == "new.example.com"

        current.enabled = False
        current.save()
        assert EmailConfig.get_current() == current

    def test_send_failure_returns_false(self, application):
        EmailConfig.objects.create(host="h", port=25, username="u", enabled=True)
        with patch("notifications.services.EmailBackend") as mock_backend:
            mock_backend.return_value.send_messages.side_effect = Exception("smtp down")
            assert send_email("t", "b", ["a@b.com"]) is False


class TestAdminEmails:
    def test_returns_staff_emails(self, django_user_model):
        django_user_model.objects.create_user(username="a", password="x", is_staff=True, email="a@x.com")
        django_user_model.objects.create_user(username="b", password="x", is_staff=False, email="b@x.com")
        emails = admin_emails()
        assert "a@x.com" in emails
        assert "b@x.com" not in emails


class TestNotify:
    def test_new_application_no_config_no_crash(self, application):
        assert notify_new_application(application) is False

    def test_review_result_no_email_no_crash(self, application):
        assert notify_review_result(application) is False

    @pytest.mark.parametrize("reviewed", [False, True])
    def test_application_notification_keeps_channel_order(self, application, reviewed):
        expected = (
            ["notify_review_result", "webhook_review_result"]
            if reviewed
            else ["webhook_new_application", "notify_new_application"]
        )
        calls = []
        with (
            patch(
                "notifications.services.notify_review_result",
                side_effect=lambda app: calls.append("notify_review_result"),
            ),
            patch(
                "notifications.services.webhook_review_result",
                side_effect=lambda app: calls.append("webhook_review_result"),
            ),
            patch(
                "notifications.services.webhook_new_application",
                side_effect=lambda app: calls.append("webhook_new_application"),
            ),
            patch(
                "notifications.services.notify_new_application",
                side_effect=lambda app: calls.append("notify_new_application"),
            ),
        ):
            notify_application(application, reviewed)
        assert calls == expected

    def test_new_application_emails_admins_via_webhook(self, application):
        """工单申请通知：webhook 发送模式下收件人=管理员邮箱列表。"""
        EmailConfig.objects.create(
            host="smtp.example.com",
            port=465,
            username="u",
            enabled=True,
            send_via=EmailConfig.SEND_VIA_WEBHOOK,
            mail_webhook_url="https://hooks.example.com/webhook/mail",
            mail_webhook_token="tok",
        )
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user(
            username="admin1",
            password="x12345!",
            is_staff=True,
            is_superuser=True,
            email="admin1@x.com",
        )
        with patch("notifications.services.open_webhook_request") as mock:
            mock.return_value.__enter__.return_value.status = 200
            ok = notify_new_application(application)
        assert ok is True
        req = mock.call_args.args[0]
        import json as _json

        payload = _json.loads(req.data)
        assert "admin1@x.com" in payload["to"]
        assert "新申请" in payload["subject"]

    def test_mail_body_contains_full_details(self, application):
        """邮件正文包含完整申请详情（不只标题）：工单号/申请人/类型/内容/状态/链接。"""
        from notifications.services import _format_mail_details

        application.pk = 10001
        application.description = "需要登录使用"
        text = _format_mail_details(application)
        assert f"工单 #{application.pk}" in text
        assert "申请人：张三" in text
        assert "类型：申请服务器账号" in text
        assert "申请内容：需要登录使用" in text
        assert "状态：待审批" in text
        assert "工单链接" in text and f"applications/{application.pk}/" in text

    def test_link_fallback_does_not_create_system_config(self):
        from notifications.services import _site_base_url

        SystemConfig.objects.all().delete()
        with override_settings(GITCODE_CALLBACK_BASE_URL="https://nrm.example.com/"):
            assert _site_base_url() == "https://nrm.example.com"
        assert not SystemConfig.objects.exists()

    def test_email_settings_merged_single_tab(self):
        """设置页：SMTP 与邮件 Webhook 整合为单 tab，发送方式单选，保留总开关。"""
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse

        su = get_user_model().objects.create_user(
            username="admin", password="x12345!", is_staff=True, is_superuser=True
        )
        c = Client()
        c.force_login(su)
        html = c.get(reverse("accounts:settings")).content.decode()
        assert 'id="tab-mail-webhook"' not in html  # 无独立邮件 Webhook tab
        assert 'name="send_via" value="smtp"' in html
        assert 'name="send_via" value="webhook"' in html
        assert 'id="smtp-settings"' in html and 'id="webhook-settings"' in html
        assert 'data-switch="email"' in html  # 通知总开关保留


class TestWebhook:
    def test_no_hooks_returns_false(self, application):
        assert send_webhook("application.created", {}) is False

    def test_pushes_to_all_enabled_hooks(self, application):
        # 单例：同一作用域只保留一条，保存后旧记录被清理
        WebhookConfig.objects.create(name="h1", url="https://hooks.example.com/hook", secret="s1", enabled=True)
        WebhookConfig.objects.create(name="h2", url="https://hooks.example.com/hook2", secret="", enabled=True)
        assert WebhookConfig.objects.count() == 1
        hook = WebhookConfig.objects.get()
        assert hook.name == "h2"
        with patch("notifications.services.open_webhook_request") as mock:
            mock.return_value.__enter__.return_value.status = 200
            ok = send_webhook("application.created", {"id": 1})
        assert ok is True
        assert mock.call_count == 1  # 单例下只推送一条

    def test_disabled_hook_not_pushed(self, application):
        WebhookConfig.objects.create(name="h", url="https://hooks.example.com/hook", enabled=False)
        with patch("notifications.services.open_webhook_request") as mock:
            assert send_webhook("application.created", {}) is False
        assert mock.call_count == 0

    def test_failure_does_not_raise(self, application):
        WebhookConfig.objects.create(name="h", url="https://hooks.example.com/hook", enabled=True)
        with patch("notifications.services.open_webhook_request", side_effect=Exception("net down")):
            assert send_webhook("application.created", {}) is False

    def test_new_application_event(self, application):
        WebhookConfig.objects.create(name="h", url="https://hooks.example.com/hook", enabled=True)
        with patch("notifications.services.send_webhook", return_value=True) as mock:
            webhook_new_application(application)
        event, payload = mock.call_args.args
        assert event == "application.created"
        assert payload["title"] == "通知测试"
        assert payload["username"] == "zs"


class TestFeishuWebhook:
    """飞书/Lark 机器人：专用消息格式 + 响应体业务码解析。"""

    def test_feishu_platform_uses_msg_type_text(self):
        from notifications.services import _build_webhook_body

        body = _build_webhook_body(
            "feishu", "https://open.feishu.cn/open-apis/bot/v2/hook/abc", "test", {"message": "hi"}
        )
        import json as _json

        data = _json.loads(body)
        assert data["msg_type"] == "text"
        assert "[NRM] test" in data["content"]["text"]

    def test_feishu_platform_formats_readable_text(self):
        """飞书平台：payload 解析为可读文本，包含申请人/工号/审批链接，不再直接展示 JSON。"""
        import json as _json

        from notifications.services import _build_webhook_body

        body = _build_webhook_body(
            "feishu",
            "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            "application.reviewed",
            {
                "id": 15,
                "applicant_name": "陶源",
                "username": "taoyuan",
                "employee_id": "t00967490",
                "apply_type_display": "申请服务器账号",
                "target_server": {"name": "A2 910B3"},
                "description": "试用",
                "status": "approved",
            },
        )
        data = _json.loads(body)
        text = data["content"]["text"]
        assert "申请人：陶源（taoyuan）" in text
        assert "工号：t00967490" in text
        assert "审批链接" in text and "applications/15/" in text
        assert '"applicant_name"' not in text  # 不再直接展示原始 JSON

    def test_non_feishu_platform_keeps_generic_format(self):
        import json as _json

        from notifications.services import _build_webhook_body

        body = _build_webhook_body("generic", "https://example.com/hook", "test", {"a": 1})
        data = _json.loads(body)
        assert data["event"] == "test"
        assert data["payload"] == {"a": 1}

    def test_business_code_nonzero_reported_as_failure(self):
        """飞书业务失败（HTTP 200 但 code != 0）必须判为失败。"""
        from notifications.services import send_webhook_to

        resp = object.__new__(type("FakeResp", (), {}))
        resp.status = 200
        resp.read = lambda: b'{"code": 19001, "msg": "sign match fail"}'
        with patch(
            "notifications.services.open_webhook_request",
            return_value=__import__("contextlib").nullcontext(resp),
        ):
            ok, msg = send_webhook_to("https://open.feishu.cn/open-apis/bot/v2/hook/abc", "")
        assert ok is False
        assert "sign match fail" in msg

    def test_business_code_zero_reported_as_success(self):
        from notifications.services import send_webhook_to

        resp = object.__new__(type("FakeResp", (), {}))
        resp.status = 200
        resp.read = lambda: b'{"code": 0, "msg": "success"}'
        with patch(
            "notifications.services.open_webhook_request",
            return_value=__import__("contextlib").nullcontext(resp),
        ):
            ok, msg = send_webhook_to("https://open.feishu.cn/open-apis/bot/v2/hook/abc", "")
        assert ok is True

    def test_plain_http_200_success(self):
        from notifications.services import send_webhook_to

        resp = object.__new__(type("FakeResp", (), {}))
        resp.status = 200
        resp.read = lambda: b""
        with patch(
            "notifications.services.open_webhook_request",
            return_value=__import__("contextlib").nullcontext(resp),
        ):
            ok, msg = send_webhook_to("https://example.com/hook", "")
        assert ok is True
