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
            applicant=normal, applicant_name="甲", username="a", email="a@x.com",
            title="我的", target_server=server,
        )
        client.force_login(normal)
        resp = client.get(reverse("applications:detail", args=[app.pk]))
        assert resp.status_code == 200
        assert "甲" in resp.content.decode()

    def test_normal_user_cannot_view_others_application(self, client, normal, staff, server):
        """普通用户查看他人工单：404 防越权。"""
        app = Application.objects.create(
            applicant=staff, applicant_name="乙", username="b", email="b@x.com",
            title="别人的", target_server=server,
        )
        client.force_login(normal)
        resp = client.get(reverse("applications:detail", args=[app.pk]))
        assert resp.status_code == 404


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
        # 初始密码同步写入工单（加密存储，读取自动解密）
        assert app.initial_password == "Pass123"

    def test_provision_initial_password_shown_in_detail(self, client, staff, server):
        """开通后工单详情页展示初始密码（邮件不可用时的兜底渠道）。"""
        app = Application.objects.create(
            applicant_name="张三",
            username="zhangsan",
            email="zs@x.com",
            employee_id="E1",
            title="开通",
            target_server=server,
            status=Application.Status.APPROVED,
            initial_password="Pass123",
        )
        client.force_login(staff)
        resp = client.get(reverse("applications:detail", args=[app.pk]))
        html = resp.content.decode()
        assert "初始密码" in html
        assert "Pass123" in html
        assert "首次登录必须修改" in html

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

        with patch("notifications.services.EmailConfig.objects.first", return_value=None):
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

    def test_npu_state_cache_avoids_repeated_ssh(self, npu_server):
        """NPU 状态缓存：首次检测后走内存缓存，不重复 SSH（申请界面不卡顿）。"""
        from unittest.mock import patch

        from servers.management import clear_npu_state_cache, get_npu_state_cached

        clear_npu_state_cache()
        with patch("servers.management.detect_npu_groups", return_value=(True, ["npu", "npu0"], "检测成功")) as mock:
            state1 = get_npu_state_cached(npu_server)
            state2 = get_npu_state_cached(npu_server)  # 命中缓存
            assert mock.call_count == 1  # 仅首次 SSH
        assert state1["groups"] == ["npu", "npu0"]
        assert state2 == state1

    def test_npu_state_cache_force_refresh(self, npu_server):
        """force_refresh=True 时强制重新 SSH 检测。"""
        from unittest.mock import patch

        from servers.management import clear_npu_state_cache, get_npu_state_cached

        clear_npu_state_cache()
        with patch("servers.management.detect_npu_groups", return_value=(True, ["npu"], "a")) as mock:
            get_npu_state_cached(npu_server)
            get_npu_state_cached(npu_server, force_refresh=True)
            assert mock.call_count == 2

    def test_npu_state_cache_non_npu_no_ssh(self, server):
        """非 NPU 服务器：直接返回空缓存，不触发 SSH。"""
        from unittest.mock import patch

        from servers.management import clear_npu_state_cache, get_npu_state_cached

        clear_npu_state_cache()
        with patch("servers.management.detect_npu_groups") as mock:
            state = get_npu_state_cached(server)
            mock.assert_not_called()
        assert state["groups"] == []
        assert state["is_npu"] is False

    def test_groups_api_uses_npu_cache(self, client, staff, npu_server):
        """申请界面 groups API：NPU 卡组来自内存缓存（非库内字段覆盖）。"""
        from unittest.mock import patch

        from servers.management import clear_npu_state_cache

        clear_npu_state_cache()
        with patch("servers.management.detect_npu_groups", return_value=(True, ["npu", "npu9"], "新检测")):
            client.force_login(staff)
            resp = client.get(reverse("servers:groups_api", args=[npu_server.pk]))
        assert resp.json()["extra_groups"] == ["npu", "npu9"]  # 缓存值而非库内 npu,npu0,npu1

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
                "description": "申请NPU卡组",
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
