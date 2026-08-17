"""系统设置页测试：GitCode/邮件/Webhook 功能总开关（切换即时生效）。"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from accounts.models import Announcement, SystemConfig
from notifications.models import EmailConfig, WebhookConfig

pytestmark = pytest.mark.django_db
User = get_user_model()


def _superuser():
    return User.objects.create_user(username="admin", password="x12345!", is_staff=True, is_superuser=True)


def _normal_user():
    return User.objects.create_user(username="normal", password="x12345!")


class TestToggleSwitch:
    """开关接口：切换即时生效 + 权限控制。"""

    def test_requires_superuser(self, client):
        client.force_login(_normal_user())
        resp = client.post(reverse("accounts:toggle_switch"), {"switch": "gitcode", "enabled": "0"})
        # superuser_required 对普通用户重定向
        assert resp.status_code in (302, 403)

    def test_toggle_gitcode(self, client):
        client.force_login(_superuser())
        cfg = SystemConfig.get_singleton()
        assert cfg.gitcode_enabled is True  # 默认开启
        resp = client.post(reverse("accounts:toggle_switch"), {"switch": "gitcode", "enabled": "0"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        cfg.refresh_from_db()
        assert cfg.gitcode_enabled is False
        # 再切回开启
        client.post(reverse("accounts:toggle_switch"), {"switch": "gitcode", "enabled": "1"})
        cfg.refresh_from_db()
        assert cfg.gitcode_enabled is True

    def test_toggle_email(self, client):
        client.force_login(_superuser())
        EmailConfig.objects.create(host="old.example.com", username="old", enabled=False)
        config = EmailConfig.objects.create(host="smtp.example.com", port=465, username="u", enabled=True)
        resp = client.post(reverse("accounts:toggle_switch"), {"switch": "email", "enabled": "0"})
        assert resp.json()["ok"] is True
        config.refresh_from_db()
        assert config.enabled is False
        assert EmailConfig.get_current() == config

    def test_toggle_webhook(self, client):
        client.force_login(_superuser())
        WebhookConfig.objects.create(name="h", url="https://example.com/hook", enabled=True)
        resp = client.post(reverse("accounts:toggle_switch"), {"switch": "webhook", "enabled": "0"})
        assert resp.json()["ok"] is True
        assert WebhookConfig.objects.get().enabled is False

    def test_unknown_switch_rejected(self, client):
        client.force_login(_superuser())
        resp = client.post(reverse("accounts:toggle_switch"), {"switch": "bogus", "enabled": "1"})
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_get_method_rejected(self, client):
        client.force_login(_superuser())
        resp = client.get(reverse("accounts:toggle_switch"))
        assert resp.status_code == 405


class TestSiteBaseUrl:
    """站点地址配置：数据库优先，留空回退 settings；设置页展示回调地址一致。"""

    def test_db_value_preferred_over_settings(self):
        cfg = SystemConfig.get_singleton()
        cfg.site_base_url = "http://myhost:8888"
        cfg.save()
        assert cfg.get_site_base_url() == "http://myhost:8888"

    def test_empty_falls_back_to_settings(self):
        cfg = SystemConfig.get_singleton()
        cfg.site_base_url = ""
        cfg.save()
        # settings.GITCODE_CALLBACK_BASE_URL 由环境变量提供（无硬编码默认值）
        with override_settings(GITCODE_CALLBACK_BASE_URL="http://fallback.example.com:8000"):
            assert cfg.get_site_base_url() == "http://fallback.example.com:8000"

    def test_save_site_base_url_from_page(self, client):
        client.force_login(_superuser())
        resp = client.post(
            reverse("accounts:settings"),
            {"save_site_base_url": "1", "site_base_url": "http://nrm.example.com:8080/"},
        )
        assert resp.status_code == 302
        cfg = SystemConfig.get_singleton()
        assert cfg.site_base_url == "http://nrm.example.com:8080"  # 已去除尾部斜杠

    def test_callback_url_uses_site_base_url(self, client):
        client.force_login(_superuser())
        cfg = SystemConfig.get_singleton()
        cfg.site_base_url = "http://myhost:9999"
        cfg.save()
        html = client.get(reverse("accounts:settings")).content.decode()
        assert "http://myhost:9999/accounts/allauth/gitcode/login/callback/" in html

    def test_webhook_review_link_uses_site_base_url(self, client):
        """webhook 审批链接使用系统设置的站点地址（不再用 Site.domain）。"""
        from notifications.services import _review_link

        cfg = SystemConfig.get_singleton()
        cfg.site_base_url = "http://myhost:7777"
        cfg.save()
        link = _review_link({"id": 42})
        assert link.startswith("http://myhost:7777/applications/42/")
        assert "example.com" not in link


class TestSettingsActionDispatch:
    def test_only_first_recognized_action_runs(self, client):
        from allauth.socialaccount.models import SocialApp

        client.force_login(_superuser())
        client.post(
            reverse("accounts:settings"),
            {
                "save_site_base_url": "1",
                "site_base_url": "https://nrm.example.com/",
                "save_gitcode": "1",
                "gitcode_client_id": "must-not-run",
            },
        )

        assert SystemConfig.get_singleton().site_base_url == "https://nrm.example.com"
        assert not SocialApp.objects.filter(provider="gitcode").exists()

    def test_mail_webhook_action_preserves_blank_secrets(self, client):
        client.force_login(_superuser())
        config = EmailConfig.objects.create(
            host="smtp.example.com",
            username="mailer",
            mail_webhook_url="https://hooks.example.com/mail",
            mail_webhook_token="keep-token",
        )

        client.post(
            reverse("accounts:settings"),
            {
                "save_mail_webhook": "1",
                "send_via": EmailConfig.SEND_VIA_WEBHOOK,
                "mail_webhook_url": "",
                "mail_webhook_token": "",
            },
        )

        config.refresh_from_db()
        assert config.send_via == EmailConfig.SEND_VIA_WEBHOOK
        assert config.mail_webhook_url == "https://hooks.example.com/mail"
        assert config.mail_webhook_token == "keep-token"

    def test_gitcode_action_updates_allauth_social_app(self, client):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        client.force_login(_superuser())
        client.post(
            reverse("accounts:settings"),
            {
                "save_gitcode": "1",
                "gitcode_client_id": "client-id",
                "gitcode_client_secret": "client-secret",
            },
        )

        app = SocialApp.objects.get(provider="gitcode")
        assert (app.client_id, app.secret) == ("client-id", "client-secret")
        assert list(app.sites.all()) == [Site.objects.get_current()]

    def test_announcement_action_saves_and_pushes(self, client):
        client.force_login(_superuser())
        with patch("accounts.views.push_notices", return_value=(True, "已推送")) as push:
            client.post(
                reverse("accounts:settings"),
                {"add_announcement": "1", "content": "# 维护通知", "enabled": "on"},
            )

        announcement = Announcement.objects.get()
        assert announcement.content == "# 维护通知"
        assert announcement.enabled is True
        push.assert_called_once_with()

    def test_announcement_save_warns_when_push_fails(self, client):
        client.force_login(_superuser())
        with patch("accounts.views.push_notices", return_value=(False, "失败：测试机连接超时")):
            response = client.post(
                reverse("accounts:settings"),
                {"add_announcement": "1", "content": "# 维护通知", "enabled": "on"},
                follow=True,
            )

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert messages[0].level_tag == "warning"
        assert "公告已保存" in str(messages[0])
        assert "测试机连接超时" in str(messages[0])


class TestGlobalWebhook:
    def test_save_reuses_form_and_preserves_secret_and_switch(self, client):
        client.force_login(_superuser())
        hook = WebhookConfig.objects.create(
            name="feishu", url="https://example.com/old", secret="keep-me", enabled=False
        )

        resp = client.post(
            reverse("accounts:settings"),
            {"add_webhook": "1", "name": "generic", "url": "", "secret": ""},
        )

        assert resp.status_code == 302
        hook.refresh_from_db()
        assert hook.name == "generic"
        assert hook.url == "https://example.com/old"
        assert hook.secret == "keep-me"
        assert hook.enabled is False

    def test_save_rejects_unsafe_url(self, client):
        client.force_login(_superuser())
        client.post(
            reverse("accounts:settings"),
            {"add_webhook": "1", "name": "generic", "url": "https://127.0.0.1/hook"},
        )
        assert not WebhookConfig.objects.exists()


class TestSMTPSettings:
    def _data(self, **overrides):
        data = {
            "save_email": "1",
            "host": "smtp.example.com",
            "port": "465",
            "username": "mailer",
            "password": "",
            "from_email": "nrm@example.com",
            "use_ssl": "on",
            "verify_email": "admin@example.com",
        }
        data.update(overrides)
        return data

    def test_invalid_port_is_form_error_not_server_error(self, client):
        client.force_login(_superuser())
        with patch("accounts.views.send_smtp_code") as send_code:
            response = client.post(reverse("accounts:settings"), self._data(port="invalid"))
        assert response.status_code == 302
        send_code.assert_not_called()
        assert "pending_smtp" not in client.session

    def test_new_invalid_config_revokes_previous_verification(self, client):
        client.force_login(_superuser())
        session = client.session
        session["pending_smtp"] = {"host": "old.example.com"}
        session["smtp_verified"] = True
        session.save()

        client.post(reverse("accounts:settings"), self._data(port="invalid"))

        assert "pending_smtp" not in client.session
        assert "smtp_verified" not in client.session

    def test_blank_password_reuses_saved_value_only_for_verification(self, client):
        client.force_login(_superuser())
        EmailConfig.objects.create(host="old", username="old", password="stored-secret")
        with patch("accounts.views.send_smtp_code", return_value=True) as send_code:
            response = client.post(reverse("accounts:settings"), self._data())

        assert response.status_code == 302
        assert send_code.call_args.args[0] == "admin@example.com"
        assert send_code.call_args.args[1].args[3] == "stored-secret"
        assert client.session["pending_smtp"]["password"] == ""

    def test_verified_pending_config_saves_without_clearing_password(self, client):
        client.force_login(_superuser())
        config = EmailConfig.objects.create(host="old", username="old", password="stored-secret", enabled=True)
        session = client.session
        session["pending_smtp"] = {
            "host": "smtp.example.com",
            "port": 587,
            "username": "mailer",
            "password": "",
            "from_email": "nrm@example.com",
            "use_ssl": False,
            "verify_email": "admin@example.com",
        }
        session["smtp_verified"] = True
        session.save()

        response = client.post(reverse("accounts:settings"), {"save_email_final": "1"})

        assert response.status_code == 302
        config.refresh_from_db()
        assert (config.host, config.port, config.username) == ("smtp.example.com", 587, "mailer")
        assert config.password == "stored-secret"
        assert config.send_via == EmailConfig.SEND_VIA_SMTP
        assert config.enabled is True


class TestSettingsPageSwitches:
    """设置页各 tab 内渲染开关，并按下开关状态禁用配置区。"""

    def test_switches_rendered(self, client):
        client.force_login(_superuser())
        html = client.get(reverse("accounts:settings")).content.decode()
        assert 'data-switch="gitcode"' in html
        assert 'data-switch="email"' in html
        assert 'data-switch="webhook"' in html
        assert 'id="fieldset-gitcode"' in html
        assert 'id="fieldset-email"' in html
        assert 'id="fieldset-webhook"' in html
        # 开关在 tab 内部（panel-heading 内）
        assert 'data-switch="gitcode"' in html.split("tab-pane")[1]

    def test_disabled_state_renders_disabled_fieldset(self, client):
        """开关关闭时对应 fieldset 必须带 disabled（配置项变灰不可改）。"""
        client.force_login(_superuser())
        # 关闭 email/webhook 开关
        client.post(reverse("accounts:toggle_switch"), {"switch": "email", "enabled": "0"})
        client.post(reverse("accounts:toggle_switch"), {"switch": "webhook", "enabled": "0"})
        html = client.get(reverse("accounts:settings")).content.decode()
        assert 'id="fieldset-email" disabled' in html
        assert 'id="fieldset-webhook" disabled' in html
        # gitcode 默认开启，不应禁用
        assert 'id="fieldset-gitcode" disabled' not in html

    def test_enabled_state_renders_enabled_fieldset(self, client):
        """开关开启时 fieldset 不带 disabled。"""
        client.force_login(_superuser())
        WebhookConfig.objects.create(name="generic", url="https://example.com/hook", enabled=False)
        # 全部开关开启
        for switch in ("gitcode", "email", "webhook"):
            client.post(reverse("accounts:toggle_switch"), {"switch": switch, "enabled": "1"})
        html = client.get(reverse("accounts:settings")).content.decode()
        for f in ("gitcode", "email", "webhook"):
            assert f'id="fieldset-{f}" >' in html or f'<fieldset id="fieldset-{f}">' in html
