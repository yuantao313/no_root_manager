"""个人中心测试：资料编辑、个人 Webhook 管理（仅本人）。"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from notifications.models import WebhookConfig

pytestmark = pytest.mark.django_db

User = get_user_model()


class TestProfileEdit:
    def test_default_readonly_shows_edit_button(self, client):
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        resp = client.get(reverse("accounts:profile"))
        html = resp.content.decode()
        assert resp.status_code == 200
        # 默认只读：显示信息与编辑按钮，不显示表单
        assert "编辑个人信息" in html
        assert 'id="id_email"' not in html

    def test_edit_mode_shows_form(self, client):
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        resp = client.get(reverse("accounts:profile") + "?edit=1")
        html = resp.content.decode()
        assert resp.status_code == 200
        assert 'id="id_email"' in html
        assert "保存" in html and "取消" in html

    def test_update_email_and_name(self, client):
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        resp = client.post(
            reverse("accounts:profile"),
            {"first_name": "张", "last_name": "三", "email": "new@x.com"},
        )
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.email == "new@x.com"
        assert user.first_name == "张"
        assert user.last_name == "三"

    def test_clear_email_allowed(self, client):
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        client.post(reverse("accounts:profile"), {"first_name": "", "last_name": "", "email": ""})
        user.refresh_from_db()
        assert user.email == ""


class TestMyWebhooks:
    @pytest.fixture
    def staff(self):
        return User.objects.create_user(username="admin", password="x12345!", is_staff=True, is_superuser=True)

    @pytest.fixture
    def other(self):
        return User.objects.create_user(username="other", password="x12345!", is_staff=True, is_superuser=True)

    def test_list_only_own(self, client, staff, other):
        WebhookConfig.objects.create(name="mine", url="http://example.com/mine", owner=staff)
        WebhookConfig.objects.create(name="theirs", url="http://example.com/theirs", owner=other)
        client.force_login(staff)
        resp = client.get(reverse("notifications:my"))
        html = resp.content.decode()
        assert resp.status_code == 200
        assert "mine" in html
        assert "theirs" not in html

    def test_create_sets_owner(self, client, staff):
        client.force_login(staff)
        resp = client.post(
            reverse("notifications:my"),
            {"name": "hook1", "url": "http://example.com/hook", "enabled": "on"},
        )
        assert resp.status_code == 302
        hook = WebhookConfig.objects.get(name="hook1")
        assert hook.owner == staff

    def test_delete_own(self, client, staff):
        hook = WebhookConfig.objects.create(name="hook1", url="http://example.com/hook", owner=staff)
        client.force_login(staff)
        resp = client.post(reverse("notifications:delete", args=[hook.pk]))
        assert resp.status_code == 302
        assert not WebhookConfig.objects.filter(pk=hook.pk).exists()

    def test_delete_others_404(self, client, staff, other):
        hook = WebhookConfig.objects.create(name="theirs", url="http://example.com/theirs", owner=other)
        client.force_login(staff)
        resp = client.post(reverse("notifications:delete", args=[hook.pk]))
        assert resp.status_code == 404  # 他人数据受保护
        assert WebhookConfig.objects.filter(pk=hook.pk).exists()
