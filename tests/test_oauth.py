"""GitCode OAuth（django-allauth）测试：登录页入口、个人中心绑定展示、资料门禁。"""

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.urls import reverse

from applications.models import Application

pytestmark = pytest.mark.django_db

User = get_user_model()


class TestLoginPage:
    @pytest.fixture(autouse=True)
    def gitcode_app(self):
        """创建 GitCode SocialApp（provider 配置后登录页显示可点入口）。"""
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        app, _ = SocialApp.objects.get_or_create(
            provider="gitcode", defaults={"name": "GitCode", "client_id": "cid", "secret": "sec"}
        )
        app.sites.add(Site.objects.get_current())
        return app

    def test_gitcode_button_shown(self, client):
        resp = client.get(reverse("accounts:login"))
        html = resp.content.decode()
        assert resp.status_code == 200
        # 登录页展示 GitCode 第三方登录入口（allauth provider_login_url 链接）
        assert "使用 GitCode 登录" in html
        assert 'href="/accounts/allauth/gitcode/login/"' in html


class TestProfileBinding:
    @pytest.fixture
    def user(self):
        return User.objects.create_user(username="reg", password="x12345!", first_name="张三")

    @pytest.fixture
    def gitcode_app(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        app, _ = SocialApp.objects.get_or_create(
            provider="gitcode", defaults={"name": "GitCode", "client_id": "cid", "secret": "sec"}
        )
        app.sites.add(Site.objects.get_current())
        return app

    def test_profile_shows_bind_entry_when_unbound(self, client, user, gitcode_app):
        client.force_login(user)
        html = client.get(reverse("accounts:profile")).content.decode()
        assert "绑定 GitCode" in html
        assert "已绑定" not in html

    def test_profile_shows_bound_when_linked(self, client, user):
        SocialAccount.objects.create(user=user, provider="gitcode", uid="66dd3f876949b24baf6e093e")
        client.force_login(user)
        html = client.get(reverse("accounts:profile")).content.decode()
        assert "已绑定" in html
        assert "66dd3f876949b24baf6e093e" in html


class TestProfileGate:
    """GitCode 用户未设姓名不能提交申请（防止 gc<id> 占位身份）。"""

    def _gc_user(self):
        user = User.objects.create_user(username="gc42", password="x")
        user.set_unusable_password()
        user.save()
        SocialAccount.objects.create(user=user, provider="gitcode", uid="42")
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

    def test_normal_user_not_blocked(self, client):
        user = User.objects.create_user(username="normal", password="x12345!", first_name="李四")
        client.force_login(user)
        resp = client.post(
            reverse("applications:create"),
            {"username": "m1", "employee_id": "E1", "apply_type": "account",
             "target_server": "", "title": "t", "applied_groups": []},
        )
        # 非 GitCode 用户不受门禁限制
        assert resp.status_code == 302
        assert resp.url.endswith("/applications/my/")
