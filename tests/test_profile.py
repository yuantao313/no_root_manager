"""个人中心测试：资料直接编辑、内嵌 Webhook 管理（仅本人）。"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from notifications.models import WebhookConfig

pytestmark = pytest.mark.django_db

User = get_user_model()


class TestProfileEdit:
    def test_default_shows_readonly_and_edit_link(self, client):
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        resp = client.get(reverse("accounts:profile"))
        html = resp.content.decode()
        assert resp.status_code == 200
        # 正文样式展示（dl），右侧"编辑"链接，无资料输入框
        assert "?edit=1" in html
        assert "name=\"save_profile\"" not in html

    def test_edit_mode_shows_inputs(self, client):
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        resp = client.get(reverse("accounts:profile") + "?edit=1")
        html = resp.content.decode()
        assert resp.status_code == 200
        assert 'id="id_name"' in html  # 姓名输入框（一体化）
        assert 'id="id_email"' in html
        assert "save_profile" in html

    def test_update_name_and_email(self, client):
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        resp = client.post(
            reverse("accounts:profile"),
            {"save_profile": "1", "name": "张三", "email": "new@x.com"},
        )
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.email == "new@x.com"
        # 姓名一体化：写入 first_name
        assert user.first_name == "张三"
        assert user.last_name == ""

    def test_clear_all_allowed(self, client):
        user = User.objects.create_user(username="u1", password="x12345!", email="old@x.com")
        client.force_login(user)
        client.post(reverse("accounts:profile"), {"save_profile": "1", "name": "", "email": ""})
        user.refresh_from_db()
        assert user.email == ""
        assert user.first_name == ""


class TestMyWebhooks:
    """Webhook 管理内嵌于个人中心页。"""

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
        resp = client.get(reverse("accounts:profile"))
        html = resp.content.decode()
        assert resp.status_code == 200
        assert "我的 Webhook" in html
        assert "mine" in html
        assert "theirs" not in html

    def test_create_sets_owner(self, client, staff):
        client.force_login(staff)
        resp = client.post(
            reverse("accounts:profile"),
            {"add_webhook": "1", "name": "hook1", "url": "http://example.com/hook", "enabled": "on"},
        )
        assert resp.status_code == 302
        hook = WebhookConfig.objects.get(name="hook1")
        assert hook.owner == staff

    def test_normal_user_has_no_webhook_section(self, client):
        user = User.objects.create_user(username="normal", password="x12345!")
        client.force_login(user)
        html = client.get(reverse("accounts:profile")).content.decode()
        assert "我的 Webhook" not in html

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
