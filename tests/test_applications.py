"""申请流程测试：登录提交、权限控制、审批开通（mock SSH）、sudo 审计。"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from applications.models import Application, SudoGrant
from credentials.models import Credential
from servers.models import Server

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def staff():
    # 超级管理员：可审批任意服务器的申请（普通管理员仅能审批绑定服务器，另测）
    return User.objects.create_user(username="admin", password="x12345!", is_staff=True, is_superuser=True)


@pytest.fixture
def normal():
    return User.objects.create_user(username="normal", password="x12345!")


@pytest.fixture
def server():
    cred = Credential.objects.create(name="c1", username="root", password="p1")
    return Server.objects.create(name="web", host="10.0.0.1", port=22, credential=cred)


class TestRequireLogin:
    def test_anonymous_redirected_to_login(self, client):
        assert client.get(reverse("applications:my")).status_code == 302

    def test_anonymous_cannot_submit(self, client):
        resp = client.post(
            reverse("applications:my"),
            {
                "applicant_name": "张三",
                "username": "zhangsan",
                "email": "zs@example.com",
                "employee_id": "E001",
                "apply_type": "create",
                "target_server": "",
                "title": "开通账号",
                "description": "需要登录",
            },
        )
        assert resp.status_code == 302
        assert Application.objects.count() == 0

    def test_login_user_can_submit(self, client, normal):
        client.force_login(normal)
        resp = client.post(
            reverse("applications:my"),
            {
                "applicant_name": "张三",
                "username": "zhangsan",
                "email": "zs@example.com",
                "employee_id": "E001",
                "apply_type": "create",
                "target_server": "",
                "title": "开通账号",
                "description": "需要登录",
            },
        )
        assert resp.status_code == 302
        app = Application.objects.filter(applicant=normal).first()
        assert app is not None and app.applicant == normal  # 记录登录用户

    def test_anonymous_cannot_see_list(self, client):
        assert client.get(reverse("applications:list")).status_code == 302


class TestRegister:
    def test_register_open_to_all(self, client):
        """用户与管理员地位平等：注册对所有用户开放，注册后自动登录。"""
        resp = client.post(
            reverse("accounts:register"),
            {"username": "newuser", "password1": "Aq12345678!", "password2": "Aq12345678!"},
        )
        assert resp.status_code == 302
        user = User.objects.filter(username="newuser").first()
        assert user is not None
        assert user.is_staff is False  # 注册为普通用户
        # 已自动登录
        assert "_auth_user_id" in client.session
        # 注册后可访问"我的申请"
        assert client.get(reverse("applications:my")).status_code == 200


class TestPermission:
    def test_normal_user_cannot_see_admin_list(self, client, normal):
        client.force_login(normal)
        assert client.get(reverse("applications:list")).status_code == 302

    def test_staff_can_see_list(self, client, staff):
        client.force_login(staff)
        assert client.get(reverse("applications:list")).status_code == 200

    def test_my_applications_only_own(self, client, normal, staff):
        Application.objects.create(applicant=normal, applicant_name="甲", username="a", email="a@x.com", title="我的")
        Application.objects.create(applicant=staff, applicant_name="乙", username="b", email="b@x.com", title="别人的")
        client.force_login(normal)
        resp = client.get(reverse("applications:my"))
        html = resp.content.decode()
        assert "我的" in html
        assert "别人的" not in html


class TestReviewProvision:
    def test_approve_provisions(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="张三",
            username="zhangsan",
            email="zs@x.com",
            employee_id="E1",
            title="开通",
            target_server=server,
        )
        client.force_login(staff)
        with patch("applications.views.provision_user", return_value=(True, "Pass123", "用户已开通")):
            resp = client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})
        assert resp.status_code == 302
        app.refresh_from_db()
        assert app.status == Application.Status.APPROVED
        assert app.provisioned_at is not None

    def test_reject_does_not_provision(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="李四",
            username="lisi",
            email="ls@x.com",
            employee_id="E2",
            title="驳回",
            target_server=server,
        )
        client.force_login(staff)
        with patch("applications.views.provision_user") as mock:
            client.post(reverse("applications:review", args=[app.pk, "reject"]), {"comment": "no"})
        mock.assert_not_called()
        app.refresh_from_db()
        assert app.status == Application.Status.REJECTED

    def test_cannot_review_twice(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="王五",
            username="wang",
            email="w@x.com",
            employee_id="E3",
            title="重复",
            target_server=server,
            status=Application.Status.APPROVED,
        )
        client.force_login(staff)
        resp = client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "x"})
        assert resp.status_code == 302
        app.refresh_from_db()
        assert app.status == Application.Status.APPROVED

    def test_sudo_grant_records_audit(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="赵六",
            username="zhao",
            email="z@x.com",
            employee_id="E4",
            title="sudo",
            target_server=server,
            needs_sudo=True,
        )
        client.force_login(staff)
        with (
            patch("applications.views.provision_user", return_value=(True, "Pass123", "已开通")),
            patch("applications.views.grant_sudo", return_value=(True, "sudo", "已加入 sudo 组")),
        ):
            client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})
        grant = SudoGrant.objects.filter(application=app).first()
        assert grant is not None
        assert grant.status == SudoGrant.Status.ACTIVE
        assert grant.granted_by == staff
        app.refresh_from_db()
        # sudo 结果写入独立字段，不与开通信息混淆
        assert app.sudo_note != ""
        assert "已开通" not in app.sudo_note

    def test_sudo_grant_failure_marked_expired(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="钱七",
            username="qian",
            email="q@x.com",
            employee_id="E5",
            title="sudo失败",
            target_server=server,
            needs_sudo=True,
        )
        client.force_login(staff)
        with (
            patch("applications.views.provision_user", return_value=(True, "Pass123", "已开通")),
            patch("applications.views.grant_sudo", return_value=(False, "", "无 sudo 组")),
        ):
            client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})
        grant = SudoGrant.objects.filter(application=app).first()
        assert grant.status == SudoGrant.Status.EXPIRED


class TestNpuGroups:
    """分组选择已升级为 NPU 卡组：仅 NPU 服务器可选手组，普通服务器无分组可选。"""

    @pytest.fixture
    def npu_server(self, server):
        server.default_group = "dev,ops"
        server.is_npu = True
        server.npu_groups = "npu,npu0,npu1"
        server.save()
        return server

    def test_npu_groups_parsing(self, npu_server):
        assert npu_server.default_groups_list() == ["dev", "ops"]
        assert npu_server.npu_groups_list() == ["npu", "npu0", "npu1"]

    def test_groups_api_plain_server_empty(self, client, staff, server):
        client.force_login(staff)
        resp = client.get(reverse("servers:groups_api", args=[server.pk]))
        data = resp.json()
        assert data["extra_groups"] == []
        assert data["is_npu"] is False

    def test_groups_api_npu_server(self, client, staff, npu_server):
        client.force_login(staff)
        resp = client.get(reverse("servers:groups_api", args=[npu_server.pk]))
        data = resp.json()
        assert data["extra_groups"] == ["npu", "npu0", "npu1"]
        assert data["is_npu"] is True

    def test_valid_npu_group_accepted(self, client, npu_server):
        from applications.forms import ApplicationForm

        form = ApplicationForm(
            {
                "applicant_name": "张三",
                "username": "zs",
                "email": "z@x.com",
                "employee_id": "E1",
                "apply_type": "create",
                "target_server": str(npu_server.pk),
                "title": "t",
                "applied_groups": ["npu0"],
            }
        )
        assert form.is_valid(), form.errors
        app = form.save()
        assert app.applied_groups == "npu0"

    def test_group_rejected_on_plain_server(self, client, server):
        from applications.forms import ApplicationForm

        form = ApplicationForm(
            {
                "applicant_name": "张三",
                "username": "zs",
                "email": "z@x.com",
                "employee_id": "E1",
                "apply_type": "create",
                "target_server": str(server.pk),
                "title": "t",
                "applied_groups": ["npu0"],
            }
        )
        assert form.is_valid() is False
        assert "不支持分组选择" in form.errors["applied_groups"][0]

    def test_invalid_npu_group_rejected(self, npu_server):
        from applications.forms import ApplicationForm

        form = ApplicationForm(
            {
                "applicant_name": "张三",
                "username": "zs",
                "email": "z@x.com",
                "employee_id": "E1",
                "apply_type": "create",
                "target_server": str(npu_server.pk),
                "title": "t",
                "applied_groups": ["hacker"],
            }
        )
        assert form.is_valid() is False
        assert "不属于所选服务器" in form.errors["applied_groups"][0]

    def test_group_without_server_rejected(self):
        from applications.forms import ApplicationForm

        form = ApplicationForm(
            {
                "applicant_name": "张三",
                "username": "zs",
                "email": "z@x.com",
                "employee_id": "E1",
                "apply_type": "create",
                "target_server": "",
                "title": "t",
                "applied_groups": ["npu0"],
            }
        )
        assert form.is_valid() is False
