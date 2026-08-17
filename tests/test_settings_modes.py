"""dev / deploy 运行模式与配置文件加载测试。

通过独立子进程加载 settings，避免污染测试进程、不触碰开发数据库。
核心约定：
  - NRM_ENV=prod -> 部署模式（严格按 .env.prod 执行）
  - 其他（含未设置 / dev）-> 开发模式（宽松 + 详细日志）
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load(env_extra, cwd=None):
    """在独立子进程中加载 settings，返回 CompletedProcess。"""
    env = dict(os.environ)
    # 清理可能从父进程继承的 NRM_* 变量，保证测试确定性
    for key in list(env):
        if key.startswith("NRM_"):
            del env[key]
    env.update(env_extra)
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    # 保证 config 包可被导入（cwd 可能是临时目录）
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    code = (
        "import django\n"
        "django.setup()\n"
        "from django.conf import settings as s\n"
        "import json\n"
        "print(json.dumps({"
        "'MODE': s.MODE, 'DEBUG': s.DEBUG, 'ALLOWED_HOSTS': s.ALLOWED_HOSTS, "
        "'CSRF_TRUSTED_ORIGINS': s.CSRF_TRUSTED_ORIGINS, 'LOG_LEVEL': s.LOG_LEVEL, "
        "'GITCODE_CALLBACK_BASE_URL': s.GITCODE_CALLBACK_BASE_URL, "
        "'DB_NAME': str(s.DATABASES['default']['NAME'])}))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=str(cwd or PROJECT_ROOT),
        capture_output=True,
        text=True,
    )


def _loads(proc):
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_dev_mode_default_loads_dotenv():
    # 默认 NRM_ENV=dev -> 开发模式，加载 .env，仅信任本机 + 详细日志
    cfg = _loads(_load({}))
    assert cfg["MODE"] == "dev"
    assert cfg["DEBUG"] is True
    assert cfg["ALLOWED_HOSTS"] == ["localhost", "127.0.0.1", "[::1]"]
    assert cfg["CSRF_TRUSTED_ORIGINS"] == []
    assert cfg["LOG_LEVEL"] == "DEBUG"
    # 可选回调地址没有硬编码默认值。
    assert cfg["GITCODE_CALLBACK_BASE_URL"] == ""


def test_callback_url_read_from_env():
    # 回调基准地址完全由环境变量控制（运行环境注入优先于 .env）
    cfg = _loads(_load({"NRM_GITCODE_CALLBACK_BASE_URL": "http://env.example.com:9000"}))
    assert cfg["GITCODE_CALLBACK_BASE_URL"] == "http://env.example.com:9000"


def test_database_path_read_from_env():
    cfg = _loads(_load({"NRM_DB_PATH": "/tmp/nrm-settings-test.sqlite3"}))
    assert cfg["DB_NAME"] == "/tmp/nrm-settings-test.sqlite3"


def test_dev_mode_explicit():
    cfg = _loads(_load({"NRM_ENV": "dev"}))
    assert cfg["MODE"] == "dev"
    assert cfg["DEBUG"] is True
    assert cfg["ALLOWED_HOSTS"] == ["localhost", "127.0.0.1", "[::1]"]
    assert cfg["LOG_LEVEL"] == "DEBUG"


def test_dev_mode_can_explicitly_allow_lan_host_and_origin():
    cfg = _loads(
        _load(
            {
                "NRM_ENV": "dev",
                "NRM_ALLOWED_HOSTS": "192.168.1.20,devbox.local",
                "NRM_CSRF_TRUSTED_ORIGINS": "http://192.168.1.20:8000",
            }
        )
    )
    assert cfg["ALLOWED_HOSTS"] == ["192.168.1.20", "devbox.local"]
    assert cfg["CSRF_TRUSTED_ORIGINS"] == ["http://192.168.1.20:8000"]


def test_deploy_mode_loads_dotenv_prod():
    # NRM_ENV=prod -> 部署模式，加载 .env.prod，严格按配置解析
    cfg = _loads(
        _load(
            {
                "NRM_ENV": "prod",
                "NRM_SECRET_KEY": "test-only-secret",
                "NRM_ALLOWED_HOSTS": "your.domain.com,localhost",
                "NRM_CSRF_TRUSTED_ORIGINS": "https://your.domain.com",
                "NRM_GITCODE_CALLBACK_BASE_URL": "https://your.domain.com",
            }
        )
    )
    assert cfg["MODE"] == "deploy"
    assert cfg["DEBUG"] is False
    assert cfg["ALLOWED_HOSTS"] == ["your.domain.com", "localhost"]
    assert cfg["CSRF_TRUSTED_ORIGINS"] == ["https://your.domain.com"]
    assert cfg["LOG_LEVEL"] == "INFO"
    assert cfg["GITCODE_CALLBACK_BASE_URL"] == "https://your.domain.com"


def test_deploy_mode_env_overrides_file():
    # 运行环境已注入同名变量（override=False），不被 .env.prod 覆盖
    cfg = _loads(
        _load(
            {
                "NRM_ENV": "prod",
                "NRM_SECRET_KEY": "test-only-secret",
                "NRM_ALLOWED_HOSTS": "real.example.com",
                "NRM_DEBUG": "True",
            }
        )
    )
    assert cfg["ALLOWED_HOSTS"] == ["real.example.com"]
    assert cfg["DEBUG"] is True


def test_deploy_mode_missing_allowed_hosts_raises():
    # 临时目录运行（不加载 .env.prod），缺 NRM_ALLOWED_HOSTS -> 报错
    tmp = tempfile.mkdtemp()
    proc = _load({"NRM_ENV": "prod", "NRM_SECRET_KEY": "x"}, cwd=tmp)
    assert proc.returncode != 0
    assert "NRM_ALLOWED_HOSTS" in proc.stderr


def test_deploy_mode_missing_secret_raises():
    # 缺 NRM_SECRET_KEY -> 部署模式禁止默认开发密钥
    tmp = tempfile.mkdtemp()
    proc = _load({"NRM_ENV": "prod", "NRM_ALLOWED_HOSTS": "example.com"}, cwd=tmp)
    assert proc.returncode != 0
    assert "NRM_SECRET_KEY" in proc.stderr
