"""P0 安全功能测试：密码找回、解绑前置检查、登录限流与日志、凭据权限收敛。"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User as AuthUser
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import GitCodeBinding, LoginLog
from credentials.models import Credential
from notifications.models import EmailConfig
from servers.models import Server, ServerAdminBinding

pytestmark = pytest.mark.django_db

User = get_user_model()


class TestPasswordReset:
    def test_form_page(self, client):
        resp = client.get(reverse("accounts:password_reset"))
        assert resp.status_code == 200
        assert "发送重置链接" in resp.content.decode()

    def test_reset_email_sent_via_smtp_config(self, client):
        User.objects.create_user(username="u1", password="old123", email="u1@x.com")
        EmailConfig.objects.create(host="smtp.x.com", port=465, username="nrm", enabled=True)
        with patch("accounts.views.send_email", return_value=True) as mock:
            resp = client.post(reverse("accounts:password_reset"), {"email": "u1@x.com"})
        assert resp.status_code == 302
        assert mock.call_count == 1
        assert mock.call_args.args[2] == ["u1@x.com"]
        # 邮件正文包含渲染后的重置链接路径
        assert "/accounts/reset/" in mock.call_args.args[1]

    def test_full_reset_flow(self, client):
        """Django 6.1 两段式流程：token 校验后跳 set-password，再设置新密码。"""
        user = User.objects.create_user(username="u1", password="old123", email="u1@x.com")
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        url = reverse("accounts:password_reset_confirm", args=[uid, token])
        resp = client.get(url, follow=True)
        assert resp.status_code == 200
        # 跟随到 set-password 阶段
        sp_url = resp.redirect_chain[-1][0]
        assert "/set-password/" in sp_url
        resp = client.post(sp_url, {"new_password1": "NewPass123!", "new_password2": "NewPass123!"}, follow=True)
        user.refresh_from_db()
        assert user.check_password("NewPass123!")


class TestUnbindRequiresPassword:
    def _gc_user(self):
        user = AuthUser.objects.create_user(username="gc1", password="x")
        user.set_unusable_password()
        user.save()
        GitCodeBinding.objects.create(user=user, gitcode_id="abc123", gitcode_username="a")
        return user

    def test_unbind_blocked_without_local_password(self, client):
        user = self._gc_user()
        client.force_login(user)
        resp = client.post(reverse("accounts:gitcode_unbind"))
        assert resp.status_code == 302
        # 绑定未被删除
        assert GitCodeBinding.objects.filter(user=user).exists()

    def test_profile_shows_set_password_entry(self, client):
        user = self._gc_user()
        client.force_login(user)
        html = client.get(reverse("accounts:profile")).content.decode()
        assert "先设置本地密码" in html
        assert "action=\"/accounts/gitcode/unbind/\"" not in html

    def test_set_password_then_unbind(self, client):
        user = self._gc_user()
        client.force_login(user)
        client.post(
            reverse("accounts:set_password"),
            {"new_password1": "LocalPass123!", "new_password2": "LocalPass123!"},
        )
        user.refresh_from_db()
        assert user.has_usable_password()
        assert user.check_password("LocalPass123!")
        client.post(reverse("accounts:gitcode_unbind"))
        assert not GitCodeBinding.objects.filter(user=user).exists()


class TestLoginThrottle:
    def test_login_logged_and_locked(self, client):
        User.objects.create_user(username="u1", password="correct123")
        # 5 次失败
        for _ in range(5):
            client.post(reverse("accounts:login"), {"username": "u1", "password": "wrong"})
        assert LoginLog.objects.filter(username="u1", success=False).count() == 5
        # 锁定期内正确密码也被拒
        resp = client.post(reverse("accounts:login"), {"username": "u1", "password": "correct123"})
        assert resp.status_code == 200
        assert "_auth_user_id" not in client.session
        # 清空失败记录（模拟解锁）后可登录
        LoginLog.objects.all().delete()
        resp = client.post(reverse("accounts:login"), {"username": "u1", "password": "correct123"})
        assert resp.status_code == 302
        assert LoginLog.objects.filter(success=True).exists()


class TestCredentialVisibility:
    @pytest.fixture
    def setup(self):
        c1 = Credential.objects.create(name="c1", username="root", password="p1")
        c2 = Credential.objects.create(name="c2", username="root", password="p2")
        s1 = Server.objects.create(name="s1", host="10.0.0.1", port=22, credential=c1)
        Server.objects.create(name="s2", host="10.0.0.2", port=22, credential=c2)
        su = User.objects.create_user(username="su", password="x", is_staff=True, is_superuser=True)
        st = User.objects.create_user(username="st", password="x", is_staff=True, is_superuser=False)
        ServerAdminBinding.objects.create(server=s1, admin=st)
        return {"c1": c1, "c2": c2, "su": su, "st": st}

    def test_normal_admin_only_sees_bound(self, client, setup):
        client.force_login(setup["st"])
        html = client.get(reverse("credentials:list")).content.decode()
        assert "c1" in html
        assert "c2" not in html
        assert client.get(reverse("credentials:detail", args=[setup["c1"].pk])).status_code == 200
        assert client.get(reverse("credentials:detail", args=[setup["c2"].pk])).status_code == 404

    def test_normal_admin_cannot_create(self, client, setup):
        client.force_login(setup["st"])
        assert client.get(reverse("credentials:create")).status_code == 302

    def test_superuser_sees_all(self, client, setup):
        client.force_login(setup["su"])
        html = client.get(reverse("credentials:list")).content.decode()
        assert "c1" in html and "c2" in html
