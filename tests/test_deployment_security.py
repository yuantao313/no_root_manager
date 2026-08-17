"""生产安全设置通过隔离子进程验证，不读取或写入开发数据库。"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from servers.models import Server

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_settings(tmp_path, extra_env=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("NRM_")}
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_TESTING": "1",
            "PYTHONPATH": str(PROJECT_ROOT),
            "NRM_ENV": "prod",
            "NRM_SECRET_KEY": "deployment-test-only-secret-key-with-sufficient-length-1234567890",
            "NRM_ALLOWED_HOSTS": "nrm.example.com",
            "NRM_CSRF_TRUSTED_ORIGINS": "https://nrm.example.com",
        }
    )
    env.update(extra_env or {})
    code = (
        "import django; django.setup(); "
        "from django.conf import settings as s; import json; "
        "print(json.dumps({"
        "'ssl': s.SECURE_SSL_REDIRECT, 'session': s.SESSION_COOKIE_SECURE, "
        "'csrf': s.CSRF_COOKIE_SECURE, 'hsts': s.SECURE_HSTS_SECONDS, "
        "'static_root': str(s.STATIC_ROOT)}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_deploy_security_defaults_are_safe(tmp_path):
    values = _load_settings(tmp_path)
    assert values == {
        "ssl": True,
        "session": True,
        "csrf": True,
        "hsts": 31536000,
        "static_root": str(PROJECT_ROOT / "staticfiles"),
    }


def test_sensitive_deploy_overrides_are_explicit(tmp_path):
    values = _load_settings(
        tmp_path,
        {
            "NRM_SECURE_SSL_REDIRECT": "false",
            "NRM_SECURE_HSTS_SECONDS": "3600",
        },
    )
    assert values["ssl"] is False
    assert values["hsts"] == 3600


@pytest.mark.django_db
def test_normal_user_cannot_trigger_device_ssh_probe(client):
    user = get_user_model().objects.create_user(username="normal", password="x12345!")
    server = Server.objects.create(name="probe-target", host="10.0.0.9")
    client.force_login(user)
    with patch("servers.views.get_device_info") as probe:
        response = client.get(reverse("servers:device_api", args=[server.pk]))
    assert response.status_code == 302
    probe.assert_not_called()
