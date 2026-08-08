"""基于 cryptography 的自定义加密字段。

使用 SECRET_KEY 派生 Fernet 密钥，对敏感内容（如服务器密钥）加密落库。
"""

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class EncryptedTextField(models.TextField):
    """内容在数据库中加密存储的 TextField（Fernet）。"""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")

    def to_python(self, value):
        # 表单/序列化传入的已是明文，直接返回；仅处理数据库读取路径的解密
        return value
