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
    def test_button_always_shown(self, client):
        # 入口始终显示（未配置时点击由 gitcode_login 给出提示）
        resp = client.get(reverse("accounts:login"))
        assert "使用 GitCode 登录" in resp.content.decode()
        assert reverse("accounts:gitcode_login") in resp.content.decode()

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

    def test_hex_string_user_id(self, client):
        """GitCode 用户 id 是 24 位十六进制字符串（非数字），须正常建号。"""
        from accounts.models import GitCodeBinding

        hex_id = "66dd3f876949b24baf6e093e"
        _set_state(client, "good")
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            with patch("accounts.views.exchange_token", return_value={"access_token": "tok"}), \
                 patch("accounts.views.get_user", return_value={"id": hex_id, "email": "u@x.com"}):
                resp = client.get(reverse("accounts:gitcode_callback") + "?code=abc&state=good")
        assert resp.status_code == 302
        user = User.objects.get(username=f"gc{hex_id}")
        binding = GitCodeBinding.objects.filter(gitcode_id=hex_id).first()
        assert binding is not None
        assert binding.user == user
        assert resp.url.endswith("/accounts/profile/")


class TestProfileGate:
    """GitCode 用户未设姓名不能提交申请（防止 gc<id> 占位身份）。"""

    def _gc_user(self):
        from accounts.models import GitCodeBinding

        user = User.objects.create_user(username="gc42", password="x")
        user.set_unusable_password()
        user.save()
        GitCodeBinding.objects.create(user=user, gitcode_id=42, gitcode_username="alice")
        return user

    def test_cannot_apply_without_name(self, client):
        user = self._gc_user()
        client.force_login(user)
        resp = client.post(
            reverse("applications:create"),
            {"username": "m1", "employee_id": "E1", "apply_type": "account",
             "target_server": "", "title": "t", "applied_groups": []},
        )
        # 未设姓名被拦截，跳个人中心
        assert resp.status_code == 302
        assert resp.url.endswith("/accounts/profile/")
        from applications.models import Application
        assert not Application.objects.exists()

    def test_can_apply_after_setting_name(self, client):
        user = self._gc_user()
        client.force_login(user)
        client.post(reverse("accounts:profile"), {"save_profile": "1", "name": "张三", "email": ""})
        user.refresh_from_db()
        assert user.first_name == "张三"
        resp = client.post(
            reverse("applications:create"),
            {"username": "m1", "employee_id": "E1", "apply_type": "account",
             "target_server": "", "title": "t", "applied_groups": []},
        )
        # 设置姓名后可提交
        assert resp.status_code == 302
        assert resp.url.endswith("/applications/my/")


class TestGitCodeBind:
    """已注册用户主动绑定 GitCode。"""

    @pytest.fixture
    def user(self):
        return User.objects.create_user(username="reg", password="x12345!", first_name="张三")

    def _bind_state(self, client, state="bs1"):
        s = client.session
        s["gitcode_bind_state"] = state
        s.save()

    def test_profile_shows_bind_entry(self, client, user):
        client.force_login(user)
        html = client.get(reverse("accounts:profile")).content.decode()
        assert "绑定 GitCode" in html
        assert "已绑定" not in html

    def test_bind_redirects_to_authorize(self, client, user):
        client.force_login(user)
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            resp = client.get(reverse("accounts:gitcode_bind"))
        assert resp.status_code == 302
        assert "gitcode.com/oauth/authorize" in resp.url

    def test_bind_callback_creates_binding(self, client, user):
        from accounts.models import GitCodeBinding

        client.force_login(user)
        self._bind_state(client)
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            with patch("accounts.views.exchange_token", return_value={"access_token": "tok"}), \
                 patch("accounts.views.get_user", return_value={"id": 555, "login": "gc_alice"}):
                # 绑定与登录共用统一回调，靠 bind_state 区分
                resp = client.get(reverse("accounts:gitcode_callback") + "?code=a&state=bs1")
        binding = GitCodeBinding.objects.filter(gitcode_id=555).first()
        assert binding is not None
        assert binding.user == user
        assert resp.url.endswith("/accounts/profile/")

    def test_bind_callback_duplicate_rejected(self, client, user):
        from accounts.models import GitCodeBinding

        other = User.objects.create_user(username="other", password="x")
        GitCodeBinding.objects.create(user=other, gitcode_id=555)
        client.force_login(user)
        self._bind_state(client)
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            with patch("accounts.views.exchange_token", return_value={"access_token": "tok"}), \
                 patch("accounts.views.get_user", return_value={"id": 555}):
                resp = client.get(reverse("accounts:gitcode_callback") + "?code=a&state=bs1")
        # 已被他人绑定 -> 拒绝且不建立绑定
        assert resp.url.endswith("/accounts/profile/")
        assert GitCodeBinding.objects.filter(gitcode_id=555).count() == 1

    def test_bound_user_login_via_oauth(self, client, user):
        """已注册用户绑定 GitCode 后，OAuth 登录直接进入该用户。"""
        from accounts.models import GitCodeBinding

        GitCodeBinding.objects.create(user=user, gitcode_id=777, gitcode_username="reg_gc")
        _set_state(client, "good")
        with patch("django.conf.settings.GITCODE_CLIENT_ID", "cid"):
            with patch("accounts.views.exchange_token", return_value={"access_token": "tok"}), \
                 patch("accounts.views.get_user", return_value={"id": 777}):
                resp = client.get(reverse("accounts:gitcode_callback") + "?code=abc&state=good")
        assert resp.status_code == 302
        assert resp.url.endswith("/applications/my/")
        # 登录的是绑定的已注册用户（gc777 不存在）
        assert int(client.session["_auth_user_id"]) == user.pk
        assert not User.objects.filter(username="gc777").exists()
