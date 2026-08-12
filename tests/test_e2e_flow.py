"""项目全流程 E2E（隔离测试库 + mock SSH，不连任何真实机器）。

覆盖链路：提交申请 → 管理员审批通过 → 机器开通（provision_user）
→ sudo 授予（SudoGrant 审计）→ 过期回收（expire_sudo）。
SSH 侧函数全部 mock，验证业务编排逻辑端到端正确。
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from applications.models import Application, SudoGrant
from credentials.models import Credential
from servers.models import Server

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def sync_background_tasks(monkeypatch):
    """测试环境：后台任务（开通/通知）同步执行，保证断言时机确定。"""
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
        server = Server.objects.create(name="e2e-server", host="10.0.0.9", port=22, credential=cred)
        yield {"applicant": applicant, "admin": admin, "server": server}


def test_full_flow_apply_approve_provision_sudo_expire(client, env):
    c = Client(SERVER_NAME="127.0.0.1")

    # ① 用户提交申请（申请服务器账号，服务器用户名=登录用户名，带 sudo）
    c.force_login(env["applicant"])
    resp = c.post(
        reverse("applications:my"),
        {
            "apply_type": "create",
            "target_server": str(env["server"].pk),
            "title": "E2E 开通",
            "description": "E2E 测试申请",
            "needs_sudo": "on",
            "applied_groups": [],
        },
    )
    app = Application.objects.filter(applicant=env["applicant"]).first()
    print("① 提交申请:", resp.status_code, "| 用户名:", app.username, "| 状态:", app.status)
    assert resp.status_code == 302
    assert app.username == "u1"  # 服务器用户名 = 登录用户名（不再按姓名自动推导）
    assert app.status == Application.Status.PENDING

    # ② 管理员审批通过 → mock SSH 开通 + sudo 授予
    c.force_login(env["admin"])
    with (
        patch("applications.views.provision_user", return_value=(True, "ok", "P@ssw0rd")) as m_prov,
        patch("applications.views.grant_sudo", return_value=(True, "nrm-sudo", "已加入 sudo 组")),
    ):
        resp = c.post(reverse("applications:review", args=[app.pk, "approve"]))
    app.refresh_from_db()
    print(
        "② 审批通过:",
        resp.status_code,
        "| 状态:",
        app.status,
        "| 已开通:",
        app.provisioned_at is not None,
        "| provision_user:",
        m_prov.call_count == 1,
    )
    assert app.status == Application.Status.APPROVED
    assert app.provisioned_at is not None
    assert m_prov.call_count == 1

    # ③ sudo 审计记录（当日失效）
    grant = SudoGrant.objects.get(application=app)
    print("③ sudo 授予:", grant.status, "| 当日失效:", grant.expires_at.date() == timezone.localtime().date())
    assert grant.status == SudoGrant.Status.ACTIVE
    assert grant.expires_at.date() == timezone.localtime().date()
    assert grant.server == env["server"] and grant.username == "u1"

    # ④ 到期回收（expire_sudo 管理命令，mock SSH 撤销）
    grant.expires_at = timezone.now()
    grant.save(update_fields=["expires_at"])
    from applications.management.commands.expire_sudo import Command as ExpireSudo

    with patch("applications.management.commands.expire_sudo.revoke_sudo", return_value=(True, "已撤销")) as m_rev:
        ExpireSudo().handle()
    grant.refresh_from_db()
    print(
        "④ 过期回收:", grant.status, "| 已撤销:", grant.revoked_at is not None, "| revoke_sudo:", m_rev.call_count == 1
    )
    assert grant.status == SudoGrant.Status.EXPIRED
    assert grant.revoked_at is not None
    assert m_rev.call_count == 1
