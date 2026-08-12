"""前端操作级 E2E（隔离测试库）：模拟用户在页面上的完整操作流程。

链路：注册→登录→我的申请页提交申请→管理员登录→申请列表/详情→
审批通过→状态流转与页面展示验证。
SSH 开通环节 mock（避免测试在已注册的真实机器上执行改动），
前端交互流程全部真实模拟（走页面 URL 与表单提交）。
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import reverse

from applications.models import Application
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
def server():
    cred = Credential.objects.create(name="私人机", username="root", password="p")
    return Server.objects.create(name="我的机器", host="10.0.0.9", port=22, credential=cred)


def test_frontend_apply_and_approve_flow(client, server):
    with override_settings(ALLOWED_HOSTS=["127.0.0.1"]):
        c = Client(SERVER_NAME="127.0.0.1")

        # ① 注册用户（前端注册页，注册信息齐全）
        resp = c.post(
            reverse("accounts:register"),
            {
                "username": "zhangsan",
                "first_name": "张三",
                "employee_id": "E10001",
                "email": "zhangsan@example.com",
                "password1": "x12345!abc",
                "password2": "x12345!abc",
            },
            follow=True,
        )
        applicant = User.objects.get(username="zhangsan")
        print("① 注册并登录:", resp.status_code, "| 用户:", applicant.username)

        # ② 进入"我的申请"页（合并页：新建申请 + 我的申请）
        resp = c.get(reverse("applications:my"))
        html = resp.content.decode()
        print(
            "② 我的申请页:",
            resp.status_code,
            "| 含新建申请:",
            "新建申请" in html,
            "| 含申请类型:",
            "申请服务器账号" in html,
        )
        assert resp.status_code == 200 and "新建申请" in html

        # ③ 页面提交申请（模拟前端表单：选择目标服务器、申请服务器账号）
        resp = c.post(
            reverse("applications:my"),
            {
                "apply_type": "create",
                "target_server": str(server.pk),
                "title": "前端流程测试",
                "description": "通过前端页面提交",
                "applied_groups": [],
            },
        )
        app = Application.objects.filter(applicant=applicant).first()
        print("③ 提交申请:", resp.status_code, "| 状态:", app.status, "| 申请人:", app.applicant_name)
        assert app.status == Application.Status.PENDING

        # ④ 管理员登录（前端登录页）
        User.objects.create_user(username="admin1", password="x12345!abc", is_staff=True, is_superuser=True)
        c.post(reverse("accounts:login"), {"username": "admin1", "password": "x12345!abc"})
        # 申请列表页
        resp = c.get(reverse("applications:list"))
        print("④ 管理员申请列表:", resp.status_code, "| 含申请:", "通过前端页面提交" in resp.content.decode())
        assert "通过前端页面提交" in resp.content.decode()

        # ⑤ 申请详情页
        resp = c.get(reverse("applications:detail", args=[app.pk]))
        print("⑤ 申请详情:", resp.status_code, "| 含申请人:", "张三" in resp.content.decode())
        assert "张三" in resp.content.decode()

        # ⑥ 审批通过（页面表单提交，SSH 开通 mock）
        with patch("applications.views.provision_user", return_value=(True, "ok", "P@ssw0rd")):
            resp = c.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "同意"}, follow=True)
        app.refresh_from_db()
        print("⑥ 审批通过:", resp.status_code, "| 状态:", app.status, "| 已开通时间:", app.provisioned_at is not None)
        assert app.status == Application.Status.APPROVED
        assert app.provisioned_at is not None

        # ⑦ 申请人页面能看到审批结果
        c.force_login(applicant)
        resp = c.get(reverse("applications:my"))
        html = resp.content.decode()
        print("⑦ 申请人回看:", resp.status_code, "| 含已通过状态:", "已通过" in html or "approved" in html)
        assert "已通过" in html or "approved" in html
