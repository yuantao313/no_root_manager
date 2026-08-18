"""个人中心测试：资料直接编辑、内嵌 Webhook 管理（仅本人）。"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.forms import ProfileForm, RegisterForm
from accounts.models import UserProfile
from notifications.models import WebhookConfig

pytestmark = pytest.mark.django_db

User = get_user_model()


class TestProfileEdit:
    def test_profile_form_commit_false_does_not_write(self):
        user = User.objects.create_user(username="u1", email="old@x.com")
        form = ProfileForm(
            {"name": "张三", "employee_id": "E001", "email": "new@x.com", "code": ""},
            instance=user,
        )

        assert form.is_valid()
        saved_user = form.save(commit=False)

        assert saved_user.first_name == "张三"
        assert saved_user.email == "new@x.com"
        user.refresh_from_db()
        assert user.first_name == ""
        assert user.email == "old@x.com"
        assert not UserProfile.objects.filter(user=user).exists()

    def test_register_form_commit_false_does_not_write(self):
        form = RegisterForm(
            {
                "username": "new_user",
                "first_name": "张三",
                "employee_id": "E001",
                "email": "new@x.com",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
            }
        )

        assert form.is_valid()
        user = form.save(commit=False)

        assert user.pk is None
        assert user.first_name == "张三"
        assert user.email == "new@x.com"
        assert not User.objects.filter(username="new_user").exists()
        assert not UserProfile.objects.exists()

    def test_profile_save_rolls_back_user_when_profile_write_fails(self):
        user = User.objects.create_user(username="u1", email="old@x.com")
        form = ProfileForm(
            {"name": "张三", "employee_id": "E001", "email": "new@x.com", "code": ""},
            instance=user,
        )

        assert form.is_valid()
        with (
            patch("accounts.forms.UserProfile.objects.update_or_create", side_effect=RuntimeError),
            pytest.raises(RuntimeError),
        ):
            form.save()

        user.refresh_from_db()
        assert user.first_name == ""
        assert user.email == "old@x.com"

    def test_profile_inline_edit_controls(self, client):
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com", first_name="张三")
        client.force_login(user)
        resp = client.get(reverse("accounts:profile"))
        html = resp.content.decode()
        assert resp.status_code == 200
        # 正文展示 + 每个字段右侧"编辑"链接 + 就地输入框（隐藏）
        # 行内编辑 JS 已抽离到 static/js/app.js（页面引用静态文件）
        assert "张三" in html
        assert "field-edit" in html
        assert 'id="field-name"' in html
        assert 'id="field-email"' in html
        assert "nrm-profile-table" in html
        assert "/static/js/app.js" in html

    def test_auth_forms_use_shared_axis_layout(self, client):
        for route in ("accounts:login", "accounts:register", "accounts:password_reset"):
            response = client.get(reverse(route))
            assert response.status_code == 200
            assert 'class="nrm-form-grid"' in response.content.decode()

    def test_update_name_without_email_change(self, client):
        # 仅改姓名（邮箱不变）无需验证码
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        resp = client.post(
            reverse("accounts:profile"),
            {"save_profile": "1", "name": "张三", "email": "old@x.com", "code": ""},
        )
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.email == "old@x.com"
        assert user.first_name == "张三"
        assert user.last_name == ""

    def test_update_email_requires_code(self, client):
        # 修改邮箱必须填写验证码，错误/缺失验证码拒绝，正确验证码通过
        from unittest.mock import patch

        from accounts.email_verify import send_user_email_code
        from accounts.models import EmailVerification

        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        # 无验证码 -> 拒绝
        client.post(
            reverse("accounts:profile"), {"save_profile": "1", "name": "张三", "email": "new@x.com", "code": ""}
        )
        user.refresh_from_db()
        assert user.email == "old@x.com"
        # 错误验证码 -> 拒绝
        client.post(
            reverse("accounts:profile"), {"save_profile": "1", "name": "张三", "email": "new@x.com", "code": "000000"}
        )
        user.refresh_from_db()
        assert user.email == "old@x.com"
        # 正确验证码 -> 通过
        with (
            patch("accounts.email_verify.generate_code", return_value="654321"),
            patch("accounts.email_verify.send_email", return_value=True),
        ):
            send_user_email_code("new@x.com", user)
        assert EmailVerification.objects.filter(email="new@x.com", purpose="user_email", user=user).exists()
        client.post(
            reverse("accounts:profile"),
            {"save_profile": "1", "name": "张三", "email": "new@x.com", "code": "654321"},
        )
        user.refresh_from_db()
        assert user.email == "new@x.com"
        assert user.first_name == "张三"

    def test_send_email_code_ajax(self, client):
        user = User.objects.create_user(username="u1", email="old@x.com")
        client.force_login(user)

        with patch("accounts.views.send_user_email_code", return_value=True) as send_code:
            response = client.post(reverse("accounts:send_email_code_ajax"), {"email": "new@x.com"})

        assert response.json() == {"ok": True, "error": "", "cooldown": 60}
        send_code.assert_called_once_with("new@x.com", user)
        assert client.session["email_code_sent_at"]


class TestMyWebhooks:
    """Webhook 管理内嵌于个人中心页。"""

    @pytest.fixture
    def staff(self):
        return User.objects.create_user(username="admin", password="x12345!", is_staff=True, is_superuser=True)

    @pytest.fixture
    def other(self):
        return User.objects.create_user(username="other", password="x12345!", is_staff=True, is_superuser=True)

    def test_list_only_own(self, client, staff, other):
        WebhookConfig.objects.create(name="feishu", url="https://example.com/mine", owner=staff)
        WebhookConfig.objects.create(name="feishu", url="https://example.com/theirs", owner=other)
        client.force_login(staff)
        resp = client.get(reverse("accounts:profile"))
        html = resp.content.decode()
        assert resp.status_code == 200
        assert "我的 Webhook" in html
        # 平台下拉展示飞书选项；他人的 webhook 不可见
        assert "飞书" in html
        assert "https://example.com/theirs" not in html

    def test_create_sets_owner(self, client, staff):
        client.force_login(staff)
        resp = client.post(
            reverse("accounts:profile"),
            {"add_webhook": "1", "name": "feishu", "url": "https://example.com/hook", "enabled": "on"},
        )
        assert resp.status_code == 302
        hook = WebhookConfig.objects.get(name="feishu")
        assert hook.owner == staff

    def test_edit_preserves_secret_when_blank(self, client, staff):
        hook = WebhookConfig.objects.create(name="feishu", url="https://example.com/old", secret="keep-me", owner=staff)
        client.force_login(staff)

        client.post(
            reverse("accounts:profile"),
            {"add_webhook": "1", "name": "generic", "url": "", "secret": "", "enabled": "on"},
        )

        hook.refresh_from_db()
        assert hook.name == "generic"
        assert hook.url == "https://example.com/old"
        assert hook.secret == "keep-me"

    def test_normal_user_has_no_webhook_section(self, client):
        user = User.objects.create_user(username="normal", password="x12345!")
        client.force_login(user)
        html = client.get(reverse("accounts:profile")).content.decode()
        assert "我的 Webhook" not in html

    def test_delete_own(self, client, staff):
        hook = WebhookConfig.objects.create(name="hook1", url="https://example.com/hook", owner=staff)
        client.force_login(staff)
        resp = client.post(reverse("notifications:delete", args=[hook.pk]))
        assert resp.status_code == 302
        assert not WebhookConfig.objects.filter(pk=hook.pk).exists()

    def test_delete_others_404(self, client, staff, other):
        hook = WebhookConfig.objects.create(name="theirs", url="https://example.com/theirs", owner=other)
        client.force_login(staff)
        resp = client.post(reverse("notifications:delete", args=[hook.pk]))
        assert resp.status_code == 404  # 他人数据受保护
        assert WebhookConfig.objects.filter(pk=hook.pk).exists()

    def test_test_webhook_success_message(self, client, staff):
        """测试推送成功：提示成功且不保存配置。"""
        client.force_login(staff)
        with patch("accounts.views.send_webhook_to", return_value=(True, "推送成功（HTTP 200）")) as mock:
            resp = client.post(
                reverse("accounts:profile"),
                {"test_webhook": "1", "name": "feishu", "url": "https://example.com/hook", "secret": ""},
            )
        assert resp.status_code == 302
        mock.assert_called_once_with("https://example.com/hook", "", platform="feishu")
        assert not WebhookConfig.objects.filter(owner=staff).exists()  # 测试不落库

    def test_test_webhook_failure_message(self, client, staff):
        """测试推送失败：提示失败信息。"""
        client.force_login(staff)
        with patch("accounts.views.send_webhook_to", return_value=(False, "推送失败：connection refused")):
            resp = client.post(
                reverse("accounts:profile"),
                {"test_webhook": "1", "name": "feishu", "url": "https://example.com/hook", "secret": ""},
            )
        assert resp.status_code == 302
        assert not WebhookConfig.objects.filter(owner=staff).exists()
