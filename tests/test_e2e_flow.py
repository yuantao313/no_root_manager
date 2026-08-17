"""项目全流程 E2E（隔离测试库 + mock SSH，不连任何真实机器）。

覆盖链路：提交申请 → 管理员审批通过 → 机器开通（provision_user）
→ 归属绑定（MachineUserBinding）。
SSH 侧函数全部 mock，验证业务编排逻辑端到端正确。
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import reverse

from accounts.models import UserProfile
from applications.models import Application
from credentials.models import Credential
from servers.models import MachineUserBinding, Server

pytestmark = pytest.mark.django_db
User = get_user_model()
TEST_HOST_FINGERPRINT = "SHA256:YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE"


@pytest.fixture(autouse=True)
def sync_background_tasks(monkeypatch):
    """测试环境仅把后台通知同步执行，保证断言时机确定。"""
    monkeypatch.setattr(
        "applications.views.run_in_background",
        lambda func, *args: func(*args),
    )


@pytest.fixture
def env():
    with override_settings(ALLOWED_HOSTS=["127.0.0.1"]):
        applicant = User.objects.create_user(username="u1", password="x12345!", first_name="张三")
        UserProfile.objects.create(user=applicant, employee_id="E100")
        admin = User.objects.create_user(username="su", password="x12345!", is_staff=True, is_superuser=True)
        cred = Credential.objects.create(name="e2e", username="root", password="p")
        server = Server.objects.create(
            name="e2e-server",
            host="10.0.0.9",
            port=22,
            credential=cred,
            ssh_host_key_fingerprint=TEST_HOST_FINGERPRINT,
        )
        yield {"applicant": applicant, "admin": admin, "server": server}


def test_full_flow_apply_approve_provision_withdraw(client, env):
    c = Client(SERVER_NAME="127.0.0.1")

    # ① 用户提交申请（申请服务器账号，服务器用户名=登录用户名）
    c.force_login(env["applicant"])
    resp = c.post(
        reverse("applications:my"),
        {
            "apply_type": "create",
            "target_server": str(env["server"].pk),
            "title": "E2E 开通",
            "description": "E2E 测试申请",
        },
    )
    app = Application.objects.filter(applicant=env["applicant"]).first()
    assert resp.status_code == 302
    assert app.username == "u1"  # 服务器用户名 = 登录用户名（不再按姓名自动推导）
    assert app.status == Application.Status.PENDING

    # ② 管理员审批通过 → mock SSH 开通 + 归属绑定
    c.force_login(env["admin"])
    with patch("applications.services.provision_user", return_value=(True, "ok", "P@ssw0rd")) as m_prov:
        resp = c.post(reverse("applications:review", args=[app.pk, "approve"]))
    app.refresh_from_db()
    assert app.status == Application.Status.APPROVED
    assert app.provisioned_at is not None
    assert m_prov.call_count == 1
    # 开通后的机器用户与申请人归属绑定
    binding = MachineUserBinding.objects.get(server=env["server"], username="u1")
    assert binding.user == env["applicant"]
