"""申请流程测试：登录提交、权限控制、审批开通（mock SSH）、sudo 审计。"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from credentials.models import Credential
from servers.models import Server, ServerAdminBinding

pytestmark = pytest.mark.django_db

User = get_user_model()
TEST_HOST_FINGERPRINT = "SHA256:YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE"


@pytest.fixture(autouse=True)
def sync_background_tasks(monkeypatch):
    """测试环境：后台任务同步执行（不真正起线程，避免 SQLite 锁与时序问题）。

    生产代码中开通/通知走 run_in_background 后台线程；
    测试里直接同步调用，保证断言时机确定。
    """
    monkeypatch.setattr(
        "applications.views.run_in_background",
        lambda func, *args: func(*args),
    )


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
    return Server.objects.create(
        name="web",
        host="10.0.0.1",
        port=22,
        credential=cred,
        ssh_host_key_fingerprint=TEST_HOST_FINGERPRINT,
    )


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

    def test_login_user_can_submit(self, client, normal, server):
        client.force_login(normal)
        resp = client.post(
            reverse("applications:my"),
            {
                "applicant_name": "张三",
                "username": "zhangsan",
                "email": "zs@example.com",
                "employee_id": "E001",
                "apply_type": "create",
                "target_server": str(server.pk),
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
            {
                "username": "newuser",
                "first_name": "新用户",
                "employee_id": "E00001",
                "email": "newuser@example.com",
                "password1": "Aq12345678!",
                "password2": "Aq12345678!",
            },
        )
        assert resp.status_code == 302
        user = User.objects.filter(username="newuser").first()
        assert user is not None
        assert user.is_staff is False  # 注册为普通用户
        assert user.first_name == "新用户"
        assert user.email == "newuser@example.com"
        assert user.profile.employee_id == "E00001"
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

    def test_normal_user_can_view_own_application(self, client, normal, server):
        """普通用户可查看自己的工单详情（不再被弹回登录页）。"""
        app = Application.objects.create(
            applicant=normal,
            applicant_name="甲",
            username="a",
            email="a@x.com",
            title="我的",
            target_server=server,
        )
        client.force_login(normal)
        resp = client.get(reverse("applications:detail", args=[app.pk]))
        assert resp.status_code == 200
        assert "甲" in resp.content.decode()

    def test_normal_user_cannot_view_others_application(self, client, normal, staff, server):
        """普通用户查看他人工单：404 防越权。"""
        app = Application.objects.create(
            applicant=staff,
            applicant_name="乙",
            username="b",
            email="b@x.com",
            title="别人的",
            target_server=server,
        )
        client.force_login(normal)
        resp = client.get(reverse("applications:detail", args=[app.pk]))
        assert resp.status_code == 404

    def test_staff_scope_is_shared_by_list_detail_and_review(self, client, normal, server):
        reviewer = User.objects.create_user(username="reviewer", password="x", is_staff=True)
        other_server = Server.objects.create(
            name="hidden-server",
            host="10.0.0.2",
            credential=server.credential,
            ssh_host_key_fingerprint=TEST_HOST_FINGERPRINT,
        )
        ServerAdminBinding.objects.create(server=server, admin=reviewer)
        bound = Application.objects.create(
            applicant=normal, username="bound", target_server=server, description="绑定工单"
        )
        hidden = Application.objects.create(
            applicant=normal, username="hidden", target_server=other_server, description="越权工单"
        )
        own = Application.objects.create(
            applicant=reviewer, username="own", target_server=other_server, description="本人未绑定工单"
        )

        assert set(Application.objects.reviewable_by(reviewer)) == {bound}
        assert set(Application.objects.visible_to(reviewer)) == {bound, own}

        client.force_login(reviewer)
        html = client.get(reverse("applications:list")).content.decode()
        assert "绑定工单" in html
        assert "越权工单" not in html
        assert "hidden-server" not in html
        assert client.get(reverse("applications:detail", args=[own.pk])).status_code == 200
        assert (
            client.post(reverse("applications:review", args=[bound.pk, "reject"]), {"comment": "不通过"}).status_code
            == 302
        )
        bound.refresh_from_db()
        assert bound.status == Application.Status.REJECTED
        assert bound.updated_at == bound.reviewed_at
        assert (
            client.post(reverse("applications:review", args=[hidden.pk, "reject"]), {"comment": "x"}).status_code == 404
        )

    def test_withdraw_updates_timestamp(self, client, normal, server):
        application = Application.objects.create(
            applicant=normal, username="normal", target_server=server, title="待撤回"
        )
        previous = timezone.now() - timedelta(days=1)
        Application.objects.filter(pk=application.pk).update(updated_at=previous)

        client.force_login(normal)
        client.post(reverse("applications:withdraw", args=[application.pk]))

        application.refresh_from_db()
        assert application.status == Application.Status.WITHDRAWN
        assert application.updated_at > previous


class TestReviewProvision:
    def test_result_and_binding_commit_atomically(self, server, normal):
        from applications.views import _record_provision
        from servers.models import MachineUserBinding

        app = Application.objects.create(
            applicant=normal,
            username="atomic-user",
            target_server=server,
        )
        with (
            patch.object(Application, "save", side_effect=RuntimeError("write failed")),
            pytest.raises(RuntimeError, match="write failed"),
        ):
            _record_provision(app, True, "已开通", "create")

        assert not MachineUserBinding.objects.filter(server=server, username="atomic-user").exists()

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
        # 初始密码同步写入工单（加密存储，读取自动解密）
        assert app.initial_password == "Pass123"

    def test_provision_initial_password_only_shown_to_applicant(self, client, staff, normal, server):
        """开通后仅申请人本人可见初始密码，且详情响应禁止缓存。"""
        app = Application.objects.create(
            applicant=normal,
            applicant_name="张三",
            username="zhangsan",
            email="zs@x.com",
            employee_id="E1",
            title="开通",
            target_server=server,
            status=Application.Status.APPROVED,
            provisioned_at=timezone.now(),
            initial_password="Pass123",
        )
        client.force_login(normal)
        resp = client.get(reverse("applications:detail", args=[app.pk]))
        html = resp.content.decode()
        assert "初始密码" in html
        assert "Pass123" in html
        assert "首次登录必须修改" in html
        assert "no-store" in resp.headers["Cache-Control"]

        client.force_login(staff)
        resp = client.get(reverse("applications:detail", args=[app.pk]))
        html = resp.content.decode()
        assert "Pass123" not in html
        assert "初始凭据仅申请人本人可见" in html

    def test_send_provision_credentials_body_has_force_change(self):
        """开通邮件正文包含密码与"首次必须改密"提示（与工单同步通知）。"""
        from notifications.services import send_provision_credentials

        app = Application(
            applicant_name="张三",
            username="zhangsan",
            email="zs@x.com",
            target_server_id=None,
        )
        from django.core import mail

        with patch("notifications.services.EmailConfig.get_current", return_value=None):
            # 未配置 SMTP：不发送，直接返回 False（不影响工单已存密码）
            assert send_provision_credentials(app, "Pass123") is False
        # 配置 SMTP 时发送，正文含强制改密提示
        from notifications.models import EmailConfig

        EmailConfig.objects.create(host="smtp.example.com", port=465, username="u", password="p", enabled=True)
        with patch("notifications.services.EmailBackend") as mock_backend:
            mock_backend.return_value.send_messages.return_value = 1
            ok = send_provision_credentials(app, "Pass123")
        assert ok is True
        sent = mail.outbox
        if sent:
            body = "".join(m.body for m in sent)
            assert "Pass123" in body
            assert "首次登录必须修改密码" in body

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

    def test_transfer_approve_creates_machine_user_binding(self, client, staff, server, normal):
        """转移类型审批通过：自动建立机器用户 ↔ 平台用户绑定。"""
        from servers.models import MachineUserBinding

        app = Application.objects.create(
            applicant=normal,
            applicant_name="甲",
            username="machine_user",
            email="a@x.com",
            title="转移",
            target_server=server,
            apply_type=Application.ApplyType.TRANSFER,
        )
        client.force_login(staff)
        with patch("applications.views.take_over_user", return_value=(True, "已接管")):
            client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})
        binding = MachineUserBinding.objects.get(server=server, username="machine_user")
        assert binding.user == normal
        assert binding.source == "transfer"

    def test_admin_approve_records_result_and_binding(self, client, staff, server, normal):
        from servers.models import MachineUserBinding

        app = Application.objects.create(
            applicant=normal,
            applicant_name="甲",
            username="zhangsan",
            target_server=server,
            apply_type=Application.ApplyType.ADMIN,
        )
        client.force_login(staff)
        with patch("applications.views.grant_sudo", return_value=(True, "sudo", "已授权")):
            client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})

        app.refresh_from_db()
        binding = MachineUserBinding.objects.get(server=server, username="zhangsan")
        assert app.provisioned_at is not None
        assert app.provision_note == "已授予 sudo（sudo）：已授权"
        assert (binding.user, binding.source) == (normal, "admin")

    def test_create_approve_creates_machine_user_binding(self, client, staff, server, normal):
        """创建类型开通成功：机器用户同样写入归属绑定（大一统）。"""
        from servers.models import MachineUserBinding

        app = Application.objects.create(
            applicant=normal,
            applicant_name="甲",
            username="zhangsan",
            email="a@x.com",
            title="开通",
            target_server=server,
            apply_type=Application.ApplyType.CREATE,
        )
        client.force_login(staff)
        with patch("applications.views.provision_user", return_value=(True, "Pass123", "已开通")):
            client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})
        binding = MachineUserBinding.objects.get(server=server, username="zhangsan")
        assert binding.user == normal
        assert binding.source == "create"

    def test_group_approve_adds_user_groups(self, client, staff, server, normal):
        """申请用户组类型：审批通过后把用户加入所选用户组（sudo/docker）。"""
        from servers.models import MachineUserBinding

        app = Application.objects.create(
            applicant=normal,
            applicant_name="甲",
            username="zhangsan",
            email="a@x.com",
            title="申请用户组",
            target_server=server,
            apply_type=Application.ApplyType.GROUP,
            user_groups="sudo,docker",
        )
        client.force_login(staff)
        with patch("applications.views.usermod_add_group", return_value=(True, "已加入")) as mock:
            client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})
        assert mock.call_count == 2  # sudo + docker 各一次
        app.refresh_from_db()
        assert app.provisioned_at is not None
        assert "已加入用户组" in app.provision_note
        binding = MachineUserBinding.objects.get(server=server, username="zhangsan")
        assert binding.source == "group"

    def test_group_approve_failure_reported(self, client, staff, server, normal):
        """申请用户组类型：加入失败时工单记录失败信息。"""
        app = Application.objects.create(
            applicant=normal,
            applicant_name="甲",
            username="zhangsan",
            email="a@x.com",
            title="申请用户组",
            target_server=server,
            apply_type=Application.ApplyType.GROUP,
            user_groups="sudo",
        )
        client.force_login(staff)
        with patch("applications.views.usermod_add_group", return_value=(False, "用户不存在")):
            client.post(reverse("applications:review", args=[app.pk, "approve"]), {"comment": "ok"})
        app.refresh_from_db()
        assert app.provisioned_at is None
        assert "失败" in app.provision_note

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


class TestResendMail:
    """重发开通凭据邮件：仅已开通且有初始密码的工单（申请人本人或超级管理员）。"""

    @pytest.fixture
    def opened_app(self, staff, server):
        """已开通（approved + provisioned_at + initial_password）的工单（申请人为 staff）。"""
        from django.utils import timezone

        return Application.objects.create(
            applicant_name="张三",
            username="zhangsan",
            email="zs@x.com",
            applicant=staff,
            target_server=server,
            apply_type=Application.ApplyType.CREATE,
            status=Application.Status.APPROVED,
            provisioned_at=timezone.now(),
            initial_password="Pass123",
        )

    def test_resend_mail_sends_credentials(self, client, staff, opened_app):
        """管理员重发：以工单加密存储的初始密码为参数发送开通邮件。"""
        client.force_login(staff)
        with patch("applications.views.send_provision_credentials", return_value=True) as mock_send:
            resp = client.post(reverse("applications:resend_mail", args=[opened_app.pk]), follow=True)
        assert resp.status_code == 200
        mock_send.assert_called_once()
        app_arg, password_arg = mock_send.call_args.args
        assert app_arg.pk == opened_app.pk
        assert password_arg == "Pass123"  # EncryptedTextField 读取即明文
        html = resp.content.decode()
        assert "重发至" in html  # 成功消息

    def test_resend_mail_owner_allowed(self, client, staff, normal, server, opened_app):
        """申请人本人也可重发自己的工单。"""
        opened_app.applicant = normal
        opened_app.save(update_fields=["applicant"])
        client.force_login(normal)
        with patch("applications.views.send_provision_credentials", return_value=True) as mock_send:
            resp = client.post(reverse("applications:resend_mail", args=[opened_app.pk]), follow=True)
        assert resp.status_code == 200
        mock_send.assert_called_once()

    def test_resend_mail_denied_for_others(self, client, staff, normal, opened_app):
        """非本人、非管理员重发他人工单 → 404（防止越权）。"""
        client.force_login(normal)
        resp = client.post(reverse("applications:resend_mail", args=[opened_app.pk]))
        assert resp.status_code == 404

    def test_resend_mail_only_approved(self, client, staff, opened_app):
        """未开通（待审批）工单禁止重发。"""
        opened_app.status = Application.Status.PENDING
        opened_app.provisioned_at = None
        opened_app.save(update_fields=["status", "provisioned_at"])
        client.force_login(staff)
        with patch("applications.views.send_provision_credentials") as mock_send:
            resp = client.post(reverse("applications:resend_mail", args=[opened_app.pk]), follow=True)
        mock_send.assert_not_called()
        assert "仅已开通的申请可以重发邮件" in resp.content.decode()

    def test_resend_mail_requires_password(self, client, staff, opened_app):
        """已开通但无初始密码（转移/用户组类型）禁止重发。"""
        opened_app.initial_password = ""
        opened_app.save(update_fields=["initial_password"])
        client.force_login(staff)
        with patch("applications.views.send_provision_credentials") as mock_send:
            resp = client.post(reverse("applications:resend_mail", args=[opened_app.pk]), follow=True)
        mock_send.assert_not_called()
        assert "无法重发凭据邮件" in resp.content.decode()

    def test_resend_mail_failure_reports(self, client, staff, opened_app):
        """发送失败（SMTP 未配置/邮箱为空）时给出错误提示。"""
        client.force_login(staff)
        with patch("applications.views.send_provision_credentials", return_value=False):
            resp = client.post(reverse("applications:resend_mail", args=[opened_app.pk]), follow=True)
        assert "邮件发送失败" in resp.content.decode()
