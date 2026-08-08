"""申请流程测试：匿名提交、权限控制、审批开通（mock SSH）、sudo 审计。"""

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
    return User.objects.create_user(username="admin", password="x12345!", is_staff=True)


@pytest.fixture
def normal():
    return User.objects.create_user(username="normal", password="x12345!")


@pytest.fixture
def server():
    cred = Credential.objects.create(name="c1", username="root", password="p1")
    return Server.objects.create(name="web", host="10.0.0.1", port=22, credential=cred)


class TestAnonymousSubmit:
    def test_anonymous_can_submit(self, client):
        resp = client.post(
            reverse("applications:create"),
            {
                "applicant_name": "张三",
                "username": "zhangsan",
                "email": "zs@example.com",
                "employee_id": "E001",
                "apply_type": "account",
                "target_server": "",
                "title": "开通账号",
                "description": "需要登录",
            },
        )
        assert resp.status_code == 200  # 成功页
        app = Application.objects.get(title="开通账号")
        assert app.applicant is None  # 匿名
        assert app.applicant_name == "张三"

    def test_anonymous_cannot_see_list(self, client):
        assert client.get(reverse("applications:list")).status_code == 302


class TestPermission:
    def test_normal_user_cannot_see_admin_list(self, client, normal):
        client.force_login(normal)
        assert client.get(reverse("applications:list")).status_code == 302

    def test_staff_can_see_list(self, client, staff):
        client.force_login(staff)
        assert client.get(reverse("applications:list")).status_code == 200

    def test_my_applications_only_own(self, client, normal, staff):
        Application.objects.create(applicant=normal, applicant_name="甲", username="a",
                                   email="a@x.com", title="我的")
        Application.objects.create(applicant=staff, applicant_name="乙", username="b",
                                   email="b@x.com", title="别人的")
        client.force_login(normal)
        resp = client.get(reverse("applications:my"))
        html = resp.content.decode()
        assert "我的" in html
        assert "别人的" not in html


class TestReviewProvision:
    def test_approve_provisions_and_creates_account(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="张三", username="zhangsan", email="zs@x.com",
            employee_id="E1", title="开通", target_server=server,
        )
        client.force_login(staff)
        with patch("applications.views.provision_user", return_value=(True, "Pass123", "用户已开通")):
            resp = client.post(
                reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"}
            )
        assert resp.status_code == 302
        app.refresh_from_db()
        assert app.status == Application.Status.APPROVED
        assert app.provisioned_at is not None
        # 匿名申请者自动创建系统账户，密码即首次密码
        user = User.objects.filter(username="zhangsan").first()
        assert user is not None
        assert user.check_password("Pass123")
        assert app.applicant == user

    def test_reject_does_not_provision(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="李四", username="lisi", email="ls@x.com",
            employee_id="E2", title="驳回", target_server=server,
        )
        client.force_login(staff)
        with patch("applications.views.provision_user") as mock:
            client.post(reverse("applications:review", args=[app.pk, "reject"]), {"comment": "no"})
        mock.assert_not_called()
        app.refresh_from_db()
        assert app.status == Application.Status.REJECTED

    def test_cannot_review_twice(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="王五", username="wang", email="w@x.com",
            employee_id="E3", title="重复", target_server=server, status=Application.Status.APPROVED,
        )
        client.force_login(staff)
        resp = client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "x"})
        assert resp.status_code == 302
        app.refresh_from_db()
        assert app.status == Application.Status.APPROVED

    def test_sudo_grant_records_audit(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="赵六", username="zhao", email="z@x.com",
            employee_id="E4", title="sudo", target_server=server, needs_sudo=True,
        )
        client.force_login(staff)
        with patch("applications.views.provision_user", return_value=(True, "Pass123", "已开通")), \
             patch("applications.views.grant_sudo", return_value=(True, "sudo", "已加入 sudo 组")):
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
            applicant_name="钱七", username="qian", email="q@x.com",
            employee_id="E5", title="sudo失败", target_server=server, needs_sudo=True,
        )
        client.force_login(staff)
        with patch("applications.views.provision_user", return_value=(True, "Pass123", "已开通")), \
             patch("applications.views.grant_sudo", return_value=(False, "", "无 sudo 组")):
            client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})
        grant = SudoGrant.objects.filter(application=app).first()
        assert grant.status == SudoGrant.Status.EXPIRED

    def test_migrate_dir_passed_without_home(self, client, staff, server):
        app = Application.objects.create(
            applicant_name="孙八", username="sun", email="s@x.com",
            employee_id="E6", title="迁移", target_server=server, migrate_from_dir="/home/old/sun",
        )
        client.force_login(staff)
        with patch("applications.views.provision_user", return_value=(True, "Pass123", "已开通")) as mock, \
             patch("applications.views.migrate_home_dir", return_value=(True, "已迁移")):
            client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})
        # 申请了迁移时不预建 home
        assert mock.call_args.kwargs["with_home"] is False
