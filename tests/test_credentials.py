"""凭据模型测试：敏感字段加密落库、解密读取。"""

import pytest

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
