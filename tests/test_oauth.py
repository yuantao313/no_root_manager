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
        """配置 GitCode（allauth SocialApp 唯一配置源）：登录页显示可点入口。"""
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


class TestSocialSignupPage:
    """GitCode 首次登录确认页（socialaccount_signup）：创建新账号必须完整填写
    姓名/用户名/密码/邮箱，邮箱从 OAuth 预填。"""

    @pytest.fixture(autouse=True)
    def gitcode_app(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        app, _ = SocialApp.objects.get_or_create(
            provider="gitcode", defaults={"name": "GitCode", "client_id": "cid", "secret": "sec"}
        )
        app.sites.add(Site.objects.get_current())
        return app

    def _pending_sociallogin(self, email="zhangsan@example.com", name="张三"):
        """构造带 pending 状态的 SocialLogin（模拟 GitCode OAuth 回调返回）。"""
        from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin
        from django.test import RequestFactory

        from accounts.providers.gitcode.provider import GitCodeProvider

        app = SocialApp.objects.get(provider="gitcode")
        request = RequestFactory().get("/")
        provider = GitCodeProvider(request, app)
        user = User(username="gc123456789012345678901234", first_name=name, email=email)
        account = SocialAccount(
            provider="gitcode",
            uid="123456789012345678901234",
            extra_data={"name": name, "email": email},
        )
        return SocialLogin(user=user, account=account, provider=provider)

    def _set_pending(self, client, sociallogin):
        session = client.session
        session["socialaccount_sociallogin"] = sociallogin.serialize()
        session.save()

    def test_signup_page_shows_all_required_fields(self, client):
        self._set_pending(client, self._pending_sociallogin())
        resp = client.get(reverse("socialaccount_signup"))
        html = resp.content.decode()
        assert resp.status_code == 200
        # 姓名/工号/用户名/密码/确认密码/邮箱六个字段全部渲染
        for field_name in ["first_name", "employee_id", "username", "password1", "password2", "email"]:
            assert f'name="{field_name}"' in html
        # 邮箱从 OAuth 预填
        assert 'value="zhangsan@example.com"' in html
        assert "请完整填写以下信息" in html
        # 用户名/姓名不预填：避免以 gc<id> 占位身份进入系统
        assert 'value="gc123456789012345678901234"' not in html
        assert 'value="张三"' not in html

    def test_signup_requires_name_and_password(self, client):
        """缺姓名/密码提交：校验失败且不创建用户。"""
        self._set_pending(client, self._pending_sociallogin())
        resp = client.post(
            reverse("socialaccount_signup"),
            {"username": "zhangsan", "email": "zhangsan@example.com"},
        )
        assert resp.status_code == 200
        assert not User.objects.filter(username="zhangsan").exists()

    def test_signup_creates_user_with_name_password_email(self, client):
        """完整填写后创建用户：姓名/工号/邮箱/真实密码生效，并跳转登录。"""
        self._set_pending(client, self._pending_sociallogin())
        resp = client.post(
            reverse("socialaccount_signup"),
            {
                "first_name": "张三",
                "employee_id": "a00123456",
                "username": "zhangsan",
                "email": "zhangsan@example.com",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
            },
        )
        assert resp.status_code == 302
        user = User.objects.get(username="zhangsan")
        assert user.first_name == "张三"
        assert user.email == "zhangsan@example.com"
        # 工号写入扩展资料（申请单自动带入）
        assert user.profile.employee_id == "a00123456"
        # 密码真实可用（非 unusable）
        assert user.check_password("SecurePass123!")

    def test_signup_requires_employee_id(self, client):
        """缺工号提交：校验失败且不创建用户。"""
        self._set_pending(client, self._pending_sociallogin())
        resp = client.post(
            reverse("socialaccount_signup"),
            {
                "first_name": "张三",
                "username": "zhangsan",
                "email": "zhangsan@example.com",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
            },
        )
        assert resp.status_code == 200
        assert not User.objects.filter(username="zhangsan").exists()

    def test_signup_password_mismatch_rejected(self, client):
        self._set_pending(client, self._pending_sociallogin())
        resp = client.post(
            reverse("socialaccount_signup"),
            {
                "first_name": "张三",
                "username": "zhangsan",
                "email": "zhangsan@example.com",
                "password1": "SecurePass123!",
                "password2": "Different456!",
            },
        )
        assert resp.status_code == 200
        assert not User.objects.filter(username="zhangsan").exists()
        assert "两次输入的密码不一致" in resp.content.decode()


class TestLoginRedirect:
    """登录后跳转目标：普通用户应跳到"我的申请"（applications:my），
    避免跳到仅管理员的 applications:list 形成登录循环。"""

    @pytest.fixture
    def normal(self):
        return User.objects.create_user(username="normal", password="x12345!", first_name="张三")

    def test_login_redirect_to_my_applications(self, client, normal):
        """账号登录后跳转"我的申请"。"""
        resp = client.post(
            reverse("accounts:login"),
            {"username": "normal", "password": "x12345!"},
        )
        assert resp.status_code == 302
        assert resp.url == reverse("applications:my") or resp.url.endswith("/applications/my/")

    def test_authenticated_can_access_my_applications(self, client, normal):
        """普通用户登录后可访问"我的申请"（不存在被弹回登录的循环）。"""
        client.force_login(normal)
        resp = client.get(reverse("applications:my"))
        assert resp.status_code == 200

    def test_normal_user_not_bounced_to_login(self, client, normal):
        """普通用户访问根路径跳"我的申请"，不进入登录循环。"""
        client.force_login(normal)
        resp = client.get(reverse("index"))
        assert resp.status_code == 302
        assert resp.url == reverse("applications:my")


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
            reverse("applications:my"),
            {
                "username": "m1",
                "employee_id": "E1",
                "apply_type": "create",
                "target_server": "",
                "title": "t",
                "applied_groups": [],
            },
        )
        # 未设姓名被拦截：页面渲染"请先设置姓名"提示且不创建申请
        assert resp.status_code == 200
        assert "请先设置姓名" in resp.content.decode()
        assert not Application.objects.exists()

    def test_can_apply_after_setting_name(self, client):
        user = self._gc_user()
        client.force_login(user)
        client.post(reverse("accounts:profile"), {"save_profile": "1", "name": "张三", "email": ""})
        user.refresh_from_db()
        assert user.first_name == "张三"
        resp = client.post(
            reverse("applications:my"),
            {
                "username": "m1",
                "employee_id": "E1",
                "apply_type": "create",
                "target_server": "",
                "title": "t",
                "description": "测试申请",
                "applied_groups": [],
            },
        )
        # 设置姓名后可提交
        assert resp.status_code == 302
        assert resp.url.endswith("/applications/my/")

    def test_normal_user_not_blocked(self, client):
        user = User.objects.create_user(username="normal", password="x12345!", first_name="李四")
        client.force_login(user)
        resp = client.post(
            reverse("applications:my"),
            {
                "username": "m1",
                "employee_id": "E1",
                "apply_type": "create",
                "target_server": "",
                "title": "t",
                "description": "测试申请",
                "applied_groups": [],
            },
        )
        # 非 GitCode 用户不受门禁限制
        assert resp.status_code == 302
        assert resp.url.endswith("/applications/my/")
