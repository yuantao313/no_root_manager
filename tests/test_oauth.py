"""GitCode OAuth 第三方登录测试。"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db

User = get_user_model()

OAUTH_SETTINGS = {
    "GITCODE_CLIENT_ID": "test-client-id",
    "GITCODE_CLIENT_SECRET": "test-secret",
}


def _set_state(client, state):
    s = client.session
    s["gitcode_oauth_state"] = state
    s.save()


class TestLoginPage:
    def test_button_hidden_when_not_configured(self, client):
        resp = client.get(reverse("accounts:login"))
        assert "使用 GitCode 登录" not in resp.content.decode()

    def test_button_shown_when_configured(self, client):
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            resp = client.get(reverse("accounts:login"))
            assert "使用 GitCode 登录" in resp.content.decode()


class TestGitCodeLogin:
    def test_not_configured_redirects_login(self, client):
        resp = client.get(reverse("accounts:gitcode_login"))
        assert resp.status_code == 302
        assert resp.url.endswith("/accounts/login/")

    def test_redirects_to_authorize_with_state(self, client):
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            resp = client.get(reverse("accounts:gitcode_login"))
            assert resp.status_code == 302
            assert "gitcode.com/oauth/authorize" in resp.url
            assert "state=" in resp.url
            assert "redirect_uri=" in resp.url
            # state 已存 session 用于回调校验
            assert "gitcode_oauth_state" in client.session


class TestGitCodeCallback:
    def test_state_mismatch_rejected(self, client):
        resp = client.get(reverse("accounts:gitcode_callback") + "?code=x&state=bad")
        assert resp.status_code == 302
        assert resp.url.endswith("/accounts/login/")

    def test_missing_code_rejected(self, client):
        _set_state(client, "good")
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            resp = client.get(reverse("accounts:gitcode_callback") + "?state=good")
            assert resp.status_code == 302
            assert resp.url.endswith("/accounts/login/")

    def test_new_user_created_with_id_mapping(self, client):
        _set_state(client, "good")
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            with patch("accounts.views.exchange_token", return_value={"access_token": "tok"}), \
                 patch("accounts.views.get_user", return_value={"id": 42, "email": "u@x.com", "login": "renamed"}):
                resp = client.get(reverse("accounts:gitcode_callback") + "?code=abc&state=good")
        assert resp.status_code == 302
        # 用户名用 id 映射 gc<id>，不用 GitCode 的 login
        user = User.objects.get(username="gc42")
        assert user.email == "u@x.com"
        assert user.first_name == ""  # 首次登录后由用户设置姓名
        assert not user.has_usable_password()  # 只能通过 OAuth 登录
        # 首次登录跳个人中心引导设置姓名
        assert resp.url.endswith("/accounts/profile/")
        # 已登录
        assert int(client.session["_auth_user_id"]) == user.pk

    def test_existing_user_login(self, client):
        user = User.objects.create_user(username="gc42", password="x", email="u@x.com", first_name="张三")
        _set_state(client, "good")
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            with patch("accounts.views.exchange_token", return_value={"access_token": "tok"}), \
                 patch("accounts.views.get_user", return_value={"id": 42}):
                resp = client.get(reverse("accounts:gitcode_callback") + "?code=abc&state=good")
        assert resp.status_code == 302
        # 老用户跳我的申请
        assert resp.url.endswith("/applications/my/")
        assert int(client.session["_auth_user_id"]) == user.pk

    def test_missing_user_id_rejected(self, client):
        _set_state(client, "good")
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            with patch("accounts.views.exchange_token", return_value={"access_token": "tok"}), \
                 patch("accounts.views.get_user", return_value={"login": "noid"}):
                resp = client.get(reverse("accounts:gitcode_callback") + "?code=abc&state=good")
        assert resp.status_code == 302
        assert resp.url.endswith("/accounts/login/")
