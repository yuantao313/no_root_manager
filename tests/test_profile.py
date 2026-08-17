"""个人中心测试：资料直接编辑、内嵌 Webhook 管理（仅本人）。"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from notifications.models import WebhookConfig

pytestmark = pytest.mark.django_db

User = get_user_model()


class TestProfileEdit:
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
        assert "/static/js/app.js" in html

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
        with patch("accounts.email_verify.send_email", return_value=True):
            send_user_email_code("new@x.com", user)
        rec = EmailVerification.objects.get(email="new@x.com", purpose="user_email", user=user)
        client.post(
            reverse("accounts:profile"), {"save_profile": "1", "name": "张三", "email": "new@x.com", "code": rec.code}
        )
        user.refresh_from_db()
        assert user.email == "new@x.com"
        assert user.first_name == "张三"

    def test_send_email_code_branch(self, client):
        from unittest.mock import patch

        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        with patch("accounts.views.send_user_email_code", return_value=True) as mock:
            resp = client.post(reverse("accounts:profile"), {"send_email_code": "1", "email": "new@x.com"})
        assert resp.status_code == 302
        assert mock.call_count == 1
        assert mock.call_args.args[0] == "new@x.com"


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
