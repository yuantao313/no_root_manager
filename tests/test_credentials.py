"""凭据模型测试：敏感字段加密落库、解密读取。"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from credentials.models import Credential

pytestmark = pytest.mark.django_db


class TestCredentialEncryption:
    def test_encrypted_at_rest(self):
        cred = Credential.objects.create(
            name="测试凭据",
            username="root",
            password="SecretPass123",
            private_key="BEGIN KEY abc",
        )
        from django.db import connection

        cursor = connection.cursor()
        cursor.execute("SELECT password, private_key FROM credentials_credential WHERE id=%s", [cred.pk])
        row = cursor.fetchone()
        # 数据库中应为密文（Fernet 前缀），不含明文
        assert "SecretPass123" not in row[0]
        assert row[0].startswith("gAAAA")
        assert "BEGIN KEY" not in row[1]
        assert row[1].startswith("gAAAA")
        # 读取时自动解密
        cred.refresh_from_db()
        assert cred.password == "SecretPass123"
        assert cred.private_key == "BEGIN KEY abc"

    def test_empty_secret_ok(self):
        cred = Credential.objects.create(name="无密钥", username="nobody")
        assert cred.password == ""
        assert cred.private_key == ""

    def test_str(self):
        cred = Credential.objects.create(name="生产", username="admin")
        assert "生产" in str(cred)

    def test_admin_edit_keeps_write_only_secrets(self, client):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "x12345!")
        cred = Credential.objects.create(name="旧名称", username="root", password="password", private_key="key")
        client.force_login(admin)

        response = client.post(
            reverse("admin:credentials_credential_change", args=[cred.pk]),
            {"name": "新名称", "username": "root", "password": "", "private_key": "", "remark": ""},
        )

        assert response.status_code == 302
        assert response.url == f"{reverse('servers:list')}?tab=credentials"
        cred.refresh_from_db()
        assert (cred.name, cred.password, cred.private_key) == ("新名称", "password", "key")

    def test_admin_add_returns_to_safe_next_page(self, client):
        admin = get_user_model().objects.create_superuser("next-admin", "admin@example.com", "x12345!")
        client.force_login(admin)
        add_url = reverse("admin:credentials_credential_add")
        next_url = reverse("servers:create")

        response = client.post(
            f"{add_url}?next={next_url}",
            {"name": "返回测试", "username": "root", "password": "secret", "private_key": "", "remark": ""},
        )

        assert response.status_code == 302
        assert response.url == next_url
        assert Credential.objects.filter(name="返回测试").exists()

    def test_admin_add_page_is_renderable_from_server_form(self, client):
        admin = get_user_model().objects.create_superuser("page-admin", "admin@example.com", "x12345!")
        client.force_login(admin)

        response = client.get(
            reverse("admin:credentials_credential_add"),
            {"next": reverse("servers:create")},
        )

        html = response.content.decode()
        assert response.status_code == 200
        for field_name in ("name", "username", "password", "private_key", "remark"):
            assert f'id="id_{field_name}"' in html

    def test_admin_add_rejects_external_next_page(self, client):
        admin = get_user_model().objects.create_superuser("safe-next-admin", "admin@example.com", "x12345!")
        client.force_login(admin)

        response = client.post(
            f"{reverse('admin:credentials_credential_add')}?next=https://evil.example/",
            {"name": "外链测试", "username": "root", "password": "secret", "private_key": "", "remark": ""},
        )

        assert response.status_code == 302
        assert response.url == f"{reverse('servers:list')}?tab=credentials"
