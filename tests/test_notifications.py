"""通知服务测试：邮件/Webhook 的配置、降级与事件 payload。"""

from unittest.mock import patch

import pytest

from applications.models import Application
from notifications.models import EmailConfig, WebhookConfig
from notifications.services import (
    admin_emails,
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


class TestSendEmail:
    def test_disabled_returns_false(self, application):
        assert send_email("t", "b", ["a@b.com"]) is False

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
            mock_backend.return_value = object()
            send_email("主题", "内容", ["a@b.com"])
            # 使用配置的 host/port，而非默认 localhost
            assert mock_backend.call_args.kwargs["host"] == "smtp.example.com"
            assert mock_backend.call_args.kwargs["port"] == 587

    def test_send_failure_returns_false(self, application):
        EmailConfig.objects.create(host="h", port=25, username="u", enabled=True)
        with patch("notifications.services.EmailMessage") as mock_msg:
            mock_msg.return_value.send.side_effect = Exception("smtp down")
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


class TestWebhook:
    def test_no_hooks_returns_false(self, application):
        assert send_webhook("application.created", {}) is False

    def test_pushes_to_all_enabled_hooks(self, application):
        WebhookConfig.objects.create(name="h1", url="http://127.0.0.1:1/hook", secret="s1", enabled=True)
        WebhookConfig.objects.create(name="h2", url="http://127.0.0.1:1/hook2", secret="", enabled=True)
        WebhookConfig.objects.create(name="h3", url="http://127.0.0.1:1/hook3", enabled=False)
        with patch("urllib.request.urlopen") as mock:
            mock.return_value.__enter__.return_value.status = 200
            ok = send_webhook("application.created", {"id": 1})
        assert ok is True
        # 只推送启用的 hook
        assert mock.call_count == 2

    def test_failure_does_not_raise(self, application):
        WebhookConfig.objects.create(name="h", url="http://127.0.0.1:1/hook", enabled=True)
        with patch("urllib.request.urlopen", side_effect=Exception("net down")):
            assert send_webhook("application.created", {}) is False

    def test_new_application_event(self, application):
        WebhookConfig.objects.create(name="h", url="http://127.0.0.1:1/hook", enabled=True)
        with patch("notifications.services.send_webhook", return_value=True) as mock:
            webhook_new_application(application)
        event, payload = mock.call_args.args
        assert event == "application.created"
        assert payload["title"] == "通知测试"
        assert payload["username"] == "zs"
