"""服务器管理服务测试：提权包装、接管、开通、目录迁移、sudo。"""

from unittest.mock import patch

import pytest
from django.urls import reverse

from applications.models import Application
from credentials.models import Credential
from servers.management import (
    _sudo_wrap,
    provision_user,
    take_over_user,
)
from servers.models import MachineUserBinding, Server

pytestmark = pytest.mark.django_db


@pytest.fixture
def root_server():
    cred = Credential.objects.create(name="root", username="root", password="")
    return Server.objects.create(name="root机", host="10.0.0.1", port=22, credential=cred)


@pytest.fixture
def ubuntu_server():
    cred = Credential.objects.create(name="ubuntu", username="ubuntu", password="")
    return Server.objects.create(name="ubuntu机", host="10.0.0.2", port=22, credential=cred)


class TestSudoWrap:
    def test_root_no_wrap(self, root_server):
        assert _sudo_wrap(root_server, "useradd -m u") == "useradd -m u"

    def test_non_root_wraps_privileged(self, ubuntu_server):
        assert _sudo_wrap(ubuntu_server, "useradd -m u") == "sudo -n useradd -m u"

    def test_pipeline_second_half_wrapped(self, ubuntu_server):
        cmd = _sudo_wrap(ubuntu_server, "echo 'u:p' | chpasswd")
        assert "sudo -n chpasswd" in cmd

    def test_logical_or_not_split(self, ubuntu_server):
        cmd = _sudo_wrap(ubuntu_server, "test -d /x && echo A || echo B")
        # 逻辑或不能被拆成管道
        assert "||" in cmd
        assert " | " not in cmd

    def test_plain_command_not_wrapped(self, ubuntu_server):
        assert _sudo_wrap(ubuntu_server, "getent group x") == "getent group x"


class TestTakeOver:
    def test_empty_username(self, ubuntu_server):
        ok, msg = take_over_user(ubuntu_server, "")
        assert ok is False and "用户名为空" in msg

    def test_takeover_uses_mgmt_script(self, ubuntu_server):
        with patch("servers.management._run_mgmt", return_value=(True, "OK takeover alice", "")) as mock:
            ok, _ = take_over_user(ubuntu_server, "alice")
        assert ok is True
        # 收敛到脚本子命令 takeover
        args = mock.call_args.args[1]
        assert args == ["takeover", "alice"]

    def test_takeover_user_not_exists(self, ubuntu_server):
        """目标机用户不存在：脚本报错时明确返回，不再由 usermod 报"用户不存在"。"""
        with patch(
            "servers.management._run_mgmt",
            return_value=(False, "", "目标机器用户 ghost 不存在"),
        ) as mock:
            ok, msg = take_over_user(ubuntu_server, "ghost")
        assert ok is False
        assert "ghost" in msg
        args = mock.call_args.args[1]
        assert args == ["takeover", "ghost"]


class TestUserLock:
    @pytest.mark.parametrize(
        ("output", "state"),
        [
            ("OK toggle_lock alice state=enabled", "启用"),
            ("OK toggle_lock alice state=disabled", "禁用"),
        ],
    )
    def test_toggle_uses_target_state(self, ubuntu_server, output, state):
        from servers.management import toggle_user_lock

        with patch("servers.management._run_mgmt", return_value=(True, output, "")) as run:
            ok, message = toggle_user_lock(ubuntu_server, " alice ")

        assert ok is True
        assert message == f"用户 alice 已{state}"
        run.assert_called_once_with(ubuntu_server, ["toggle_lock", "alice"])

    def test_toggle_rejects_empty_username(self, ubuntu_server):
        from servers.management import toggle_user_lock

        with patch("servers.management._run_mgmt") as run:
            assert toggle_user_lock(ubuntu_server, " ") == (False, "用户名为空")
        run.assert_not_called()

    def test_toggle_rejects_unknown_target_response(self, ubuntu_server):
        from servers.management import toggle_user_lock

        with patch("servers.management._run_mgmt", return_value=(True, "OK", "")):
            ok, message = toggle_user_lock(ubuntu_server, "alice")

        assert ok is False
        assert "未返回有效" in message


class TestResetUserPassword:
    def test_password_is_passed_over_stdin(self, ubuntu_server):
        from servers.management import reset_user_password

        with (
            patch("servers.management.get_random_string", return_value="TemporaryPass123"),
            patch(
                "servers.management._run_mgmt",
                return_value=(True, "OK reset_password alice", ""),
            ) as run,
        ):
            ok, password, message = reset_user_password(ubuntu_server, " alice ")

        assert ok is True
        assert password == "TemporaryPass123"
        assert "强制修改" in message
        run.assert_called_once_with(
            ubuntu_server,
            ["reset_password", "alice"],
            stdin_data="alice:TemporaryPass123",
        )

    def test_failure_does_not_return_generated_password(self, ubuntu_server):
        from servers.management import reset_user_password

        with (
            patch("servers.management.get_random_string", return_value="MustNotLeak123"),
            patch("servers.management._run_mgmt", return_value=(False, "", "remote failed")),
        ):
            assert reset_user_password(ubuntu_server, "alice") == (False, "", "remote failed")

    def test_empty_username_is_rejected_before_generating_password(self, ubuntu_server):
        from servers.management import reset_user_password

        with patch("servers.management.get_random_string") as generate:
            assert reset_user_password(ubuntu_server, " ") == (False, "", "用户名为空")
        generate.assert_not_called()


class TestProvisionUser:
    def test_empty_username(self, ubuntu_server):
        ok, pwd, msg = provision_user(ubuntu_server, "")
        assert ok is False and "用户名为空" in msg

    def test_provision_uses_mgmt_script_with_groups(self, ubuntu_server):
        """开通收敛到脚本 provision：分组含 nrm_managed，密码经 stdin 传递。"""
        with patch(
            "servers.management._run_mgmt",
            return_value=(True, "OK provision carol", ""),
        ) as mock:
            ok, pwd, msg = provision_user(ubuntu_server, "carol", groups=["dev"])
        assert ok is True
        assert len(pwd) == 16
        args = mock.call_args.args[1]
        # provision <user> <groups_csv> <with_home> <force_pwd>
        assert args[0] == "provision"
        assert args[1] == "carol"
        assert "dev,nrm_managed" in args[2]
        assert args[3] == "1"  # with_home
        assert args[4] == "1"  # force_pwd_change
        # 密码经 stdin 传递（不进命令行参数）
        stdin_data = mock.call_args.kwargs.get("stdin_data")
        assert stdin_data and stdin_data.startswith("carol:")

    def test_without_home_flag(self, ubuntu_server):
        with patch(
            "servers.management._run_mgmt",
            return_value=(True, "OK provision dave", ""),
        ) as mock:
            provision_user(ubuntu_server, "dave", with_home=False)
        args = mock.call_args.args[1]
        assert args[3] == "0"  # with_home=0

    def test_force_pwd_change_off(self, ubuntu_server):
        with patch(
            "servers.management._run_mgmt",
            return_value=(True, "OK provision erin", ""),
        ) as mock:
            provision_user(ubuntu_server, "erin", force_pwd_change=False)
        args = mock.call_args.args[1]
        assert args[4] == "0"  # force_pwd_change=0


class TestManagedUsersBinding:
    """受管用户列表：显示对应的系统用户（MachineUserBinding 归属）。"""

    def test_managed_users_show_system_user(self, client, ubuntu_server, django_user_model):
        from unittest.mock import patch

        from servers.models import MachineUserBinding

        su = django_user_model.objects.create_user(username="su2", password="x12345!", is_staff=True, is_superuser=True)
        normal = django_user_model.objects.create_user(username="normal2", password="x12345!")
        MachineUserBinding.objects.create(server=ubuntu_server, username="m_user_a", user=normal, source="create")
        MachineUserBinding.objects.create(server=ubuntu_server, username="m_user_b", user=None, source="manual")
        client.force_login(su)
        with (
            patch("servers.views.list_system_users", return_value=(True, [], "ok")),
            patch(
                "servers.views.get_managed_users_cached",
                return_value=(["m_user_a", "m_user_b", "m_user_c"], "ok"),
            ),
        ):
            resp = client.get(reverse("servers:user_management", args=[ubuntu_server.pk]))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "系统用户" in html  # 列头
        # 已绑定用户：显示平台用户名链接；未绑定用户：显示"未绑定"
        assert "normal2</a>" in html
        assert "未绑定" in html
        # 未绑定的机器用户也在列表中
        assert "m_user_c" in html


class TestMotdPush:
    def test_reuses_rendered_announcement_for_all_servers(self, root_server, ubuntu_server):
        from servers.management import push_notices

        with (
            patch("servers.management._announcement_text", return_value="rendered") as render,
            patch("servers.management.write_server_motd", return_value=(True, "ok")) as write,
        ):
            ok, message = push_notices()

        assert ok is True
        assert "2 台服务器" in message
        render.assert_called_once_with()
        assert write.call_count == 2
        write.assert_any_call(root_server, "rendered")
        write.assert_any_call(ubuntu_server, "rendered")

    def test_empty_announcement_clears_existing_motd(self, ubuntu_server):
        from servers.management import push_notices

        with (
            patch("servers.management._announcement_text", return_value=""),
            patch("servers.management.write_server_motd", return_value=(True, "已清除")) as write,
        ):
            ok, message = push_notices(ubuntu_server)

        assert ok is True
        assert "1 台服务器" in message
        write.assert_called_once_with(ubuntu_server, "")

    def test_reports_partial_failure(self, root_server, ubuntu_server):
        from servers.management import push_notices

        with (
            patch("servers.management._announcement_text", return_value="rendered"),
            patch(
                "servers.management.write_server_motd",
                side_effect=[(True, "ok"), (False, "连接失败")],
            ),
        ):
            ok, message = push_notices()

        assert ok is False
        assert "1 台服务器" in message
        assert "ubuntu机：连接失败" in message


class TestUserGroups:
    """用户所属组：目标机批量查询 + 增加/删除用户组（详情页用户管理区）。"""

    def test_list_user_groups_parses(self, ubuntu_server):
        from servers.management import list_user_groups

        out = "USER_GROUPS alice nrm_managed,sudo,docker\nUSER_GROUPS bob nrm_managed\n"
        with patch("servers.management._run_mgmt", return_value=(True, out, "")) as mock:
            ok, groups_map, msg = list_user_groups(ubuntu_server, ["alice", "bob"])
        assert ok is True
        assert groups_map["alice"] == ["nrm_managed", "sudo", "docker"]
        assert groups_map["bob"] == ["nrm_managed"]
        assert "2 个用户" in msg
        assert mock.call_args.args[1] == ["list_groups", "alice,bob"]

    def test_list_user_groups_empty(self, ubuntu_server):
        from servers.management import list_user_groups

        with patch("servers.management._run_mgmt") as mock:
            ok, groups_map, _ = list_user_groups(ubuntu_server, [])
        assert ok is True and groups_map == {}
        mock.assert_not_called()

    def test_sort_user_groups(self, ubuntu_server):
        """组展示排序：排除用户本名组，nrm_managed 置顶、其他组排序。"""
        from servers.management import sort_user_groups

        priority, others = sort_user_groups("alice", ["alice", "nrm_managed", "docker", "sudo"])
        assert priority == ["nrm_managed"]
        assert others == ["docker", "sudo"]
        # 本名组被排除
        assert "alice" not in priority + others

    def test_sort_user_groups_empty(self, ubuntu_server):
        from servers.management import sort_user_groups

        priority, others = sort_user_groups("alice", [])
        assert priority == [] and others == []

    def test_sort_user_groups_nrm_only(self, ubuntu_server):
        from servers.management import sort_user_groups

        priority, others = sort_user_groups("alice", ["nrm_managed"])
        assert priority == ["nrm_managed"]
        assert others == []

    def test_add_user_group_uses_script(self, ubuntu_server):
        from servers.management import add_user_group

        with patch(
            "servers.management._run_mgmt",
            return_value=(True, "OK add_group alice group=docker", ""),
        ) as mock:
            ok, msg = add_user_group(ubuntu_server, "alice", "docker")
        assert ok is True and "docker" in msg
        assert mock.call_args.args[1] == ["add_group", "alice", "docker"]

    def test_add_user_group_invalid_name(self, ubuntu_server):
        from servers.management import add_user_group

        with patch("servers.management._run_mgmt") as mock:
            ok, msg = add_user_group(ubuntu_server, "alice", "bad group!")
        assert ok is False and "非法组名" in msg
        mock.assert_not_called()

    def test_remove_user_group_uses_script(self, ubuntu_server):
        from servers.management import remove_user_group

        with patch(
            "servers.management._run_mgmt",
            return_value=(True, "OK del_group alice group=docker", ""),
        ) as mock:
            ok, msg = remove_user_group(ubuntu_server, "alice", "docker")
        assert ok is True and "docker" in msg
        assert mock.call_args.args[1] == ["del_group", "alice", "docker"]

    def test_remove_user_group_blocks_nrm_managed(self, ubuntu_server):
        from servers.management import remove_user_group

        with patch("servers.management._run_mgmt") as mock:
            ok, msg = remove_user_group(ubuntu_server, "alice", "nrm_managed")
        assert ok is False and "不能直接移除" in msg
        mock.assert_not_called()

    def test_remove_user_group_blocks_primary_group(self, ubuntu_server):
        from servers.management import remove_user_group

        with patch("servers.management._run_mgmt") as mock:
            ok, msg = remove_user_group(ubuntu_server, "alice", "alice")
        assert ok is False and "受保护" in msg
        mock.assert_not_called()

    def test_get_user_groups_cached(self, ubuntu_server):
        from servers.management import clear_user_groups_cache, get_user_groups_cached

        clear_user_groups_cache(ubuntu_server)
        out = "USER_GROUPS alice nrm_managed\nUSER_GROUPS bob nrm_managed\n"
        with patch("servers.management._run_mgmt", return_value=(True, out, "")) as mock:
            groups_map = get_user_groups_cached(ubuntu_server, ["alice", "bob"])
            assert groups_map["alice"] == ["nrm_managed"]
            # 二次查询命中缓存，不再 SSH
            groups_map2 = get_user_groups_cached(ubuntu_server, ["alice"])
        assert mock.call_count == 1
        assert groups_map2["alice"] == ["nrm_managed"]

    def test_get_managed_users_cached_and_clear(self, ubuntu_server):
        from servers.management import clear_managed_users_cache, get_managed_users_cached

        clear_managed_users_cache(ubuntu_server)
        with patch("servers.management.list_nrm_members", return_value=(True, ["alice"], "ok")) as scan:
            assert get_managed_users_cached(ubuntu_server) == (["alice"], "ok")
            assert get_managed_users_cached(ubuntu_server) == (["alice"], "ok")
            clear_managed_users_cache(ubuntu_server)
            assert get_managed_users_cached(ubuntu_server) == (["alice"], "ok")
        assert scan.call_count == 2

    def test_detail_user_queries_share_one_snapshot_connection(self, ubuntu_server):
        from servers.management import (
            clear_managed_users_cache,
            clear_user_groups_cache,
            get_managed_users_cached,
            get_user_groups_cached,
            list_system_users,
        )

        clear_managed_users_cache(ubuntu_server)
        clear_user_groups_cache(ubuntu_server)
        output = "\n".join(
            [
                "MANAGED_USER alice",
                "USER_GROUPS alice alice,nrm_managed,docker",
                "AVAILABLE_USER bob",
            ]
        )
        with patch("servers.management._run_mgmt", return_value=(True, output, "")) as run:
            assert list_system_users(ubuntu_server)[1] == ["bob"]
            assert get_managed_users_cached(ubuntu_server)[0] == ["alice"]
            assert get_user_groups_cached(ubuntu_server, ["alice"])["alice"] == [
                "alice",
                "nrm_managed",
                "docker",
            ]

        run.assert_called_once_with(ubuntu_server, ["snapshot_users"])
        clear_managed_users_cache(ubuntu_server)
        clear_user_groups_cache(ubuntu_server)

    def test_set_user_groups_uses_one_script_call(self, ubuntu_server):
        from servers.management import set_user_groups

        with patch(
            "servers.management._run_mgmt",
            return_value=(True, "OK update_groups alice add=sudo remove=docker", ""),
        ) as run:
            ok, message = set_user_groups(
                ubuntu_server,
                "alice",
                {"nrm_managed", "sudo"},
                {"alice", "nrm_managed", "docker"},
            )

        assert ok is True
        assert "update_groups" in message
        run.assert_called_once_with(
            ubuntu_server,
            ["update_groups", "alice", "sudo", "docker"],
        )

    def test_set_user_groups_skips_ssh_without_changes(self, ubuntu_server):
        from servers.management import set_user_groups

        with patch("servers.management._run_mgmt") as run:
            ok, message = set_user_groups(
                ubuntu_server,
                "alice",
                {"nrm_managed"},
                {"nrm_managed"},
            )

        assert ok is True
        assert "无需变更" in message
        run.assert_not_called()

    def test_add_group_view_requires_superuser(self, client, ubuntu_server, django_user_model):
        """已登录的非超级管理员访问加组接口时明确返回 403。"""
        normal = django_user_model.objects.create_user(username="norm2", password="x12345!")
        client.force_login(normal)
        resp = client.post(
            reverse("servers:add_user_group", args=[ubuntu_server.pk]),
            {"username": "alice", "group": "docker"},
        )
        assert resp.status_code == 403

    def test_add_group_view_success(self, client, ubuntu_server, django_user_model):
        su = django_user_model.objects.create_user(username="su3", password="x12345!", is_staff=True, is_superuser=True)
        client.force_login(su)
        with (
            patch("servers.views.add_user_group", return_value=(True, "用户 alice 已加入 docker 组")) as mock_add,
            patch("servers.views.clear_user_groups_cache") as mock_clear,
        ):
            resp = client.post(
                reverse("servers:add_user_group", args=[ubuntu_server.pk]),
                {"username": "alice", "group": "docker"},
                follow=True,
            )
        assert resp.status_code == 200
        mock_add.assert_called_once()
        mock_clear.assert_called_once()
        assert "已加入 docker 组" in resp.content.decode()

    def test_remove_group_view_blocks_nrm_managed(self, client, ubuntu_server, django_user_model):
        su = django_user_model.objects.create_user(username="su4", password="x12345!", is_staff=True, is_superuser=True)
        client.force_login(su)
        with patch("servers.views.remove_user_group") as mock_remove:
            resp = client.post(
                reverse("servers:remove_user_group", args=[ubuntu_server.pk]),
                {"username": "alice", "group": "nrm_managed"},
                follow=True,
            )
        mock_remove.assert_not_called()
        assert "不能直接移除" in resp.content.decode()

    def test_remove_group_view_success(self, client, ubuntu_server, django_user_model):
        su = django_user_model.objects.create_user(username="su6", password="x12345!", is_staff=True, is_superuser=True)
        client.force_login(su)
        with (
            patch("servers.views.remove_user_group", return_value=(True, "用户 alice 已从 docker 组移除")) as mock_rm,
            patch("servers.views.clear_user_groups_cache") as mock_clear,
        ):
            resp = client.post(
                reverse("servers:remove_user_group", args=[ubuntu_server.pk]),
                {"username": "alice", "group": "docker"},
                follow=True,
            )
        assert resp.status_code == 200
        mock_rm.assert_called_once()
        mock_clear.assert_called_once()
        assert "已从 docker 组移除" in resp.content.decode()

    def test_update_user_groups_view_diff(self, client, ubuntu_server, django_user_model):
        """批量切换接口把目标组全集交给单次 SSH 服务。"""
        su = django_user_model.objects.create_user(username="su7", password="x12345!", is_staff=True, is_superuser=True)
        client.force_login(su)
        with (
            patch(
                "servers.views.get_user_groups_cached",
                return_value={"alice": ["nrm_managed", "docker"]},
            ),
            patch("servers.views.set_user_groups", return_value=(True, "已更新 alice 的用户组配置")) as update,
        ):
            resp = client.post(
                reverse("servers:update_user_groups", args=[ubuntu_server.pk]),
                {"username": "alice", "groups": "nrm_managed,sudo"},
                follow=True,
            )
        assert resp.status_code == 200
        update.assert_called_once_with(
            ubuntu_server,
            "alice",
            {"nrm_managed", "sudo"},
            {"nrm_managed", "docker"},
        )
        assert "已更新 alice 的用户组配置" in resp.content.decode()

    def test_update_user_groups_keeps_nrm_managed(self, client, ubuntu_server, django_user_model):
        """前端即使未提交 nrm_managed，后端也强制保留标识组不移出。"""
        su = django_user_model.objects.create_user(username="su8", password="x12345!", is_staff=True, is_superuser=True)
        client.force_login(su)
        with (
            patch(
                "servers.views.get_user_groups_cached",
                return_value={"alice": ["nrm_managed", "docker"]},
            ),
            patch("servers.views.set_user_groups", return_value=(True, "ok")) as update,
        ):
            resp = client.post(
                reverse("servers:update_user_groups", args=[ubuntu_server.pk]),
                {"username": "alice", "groups": "sudo"},  # 未包含 nrm_managed
                follow=True,
            )
        assert resp.status_code == 200
        update.assert_called_once_with(
            ubuntu_server,
            "alice",
            {"sudo"},
            {"nrm_managed", "docker"},
        )

    def test_update_user_groups_keeps_primary_group(self, client, ubuntu_server, django_user_model):
        su = django_user_model.objects.create_user(username="su10", password="x12345!", is_superuser=True)
        client.force_login(su)
        with (
            patch(
                "servers.views.get_user_groups_cached",
                return_value={"alice": ["alice", "nrm_managed", "docker"]},
            ),
            patch("servers.views.set_user_groups", return_value=(True, "ok")) as update,
        ):
            client.post(
                reverse("servers:update_user_groups", args=[ubuntu_server.pk]),
                {"username": "alice", "groups": "nrm_managed"},
            )

        update.assert_called_once_with(
            ubuntu_server,
            "alice",
            {"nrm_managed"},
            {"alice", "nrm_managed", "docker"},
        )

    def test_update_user_groups_requires_superuser(self, client, ubuntu_server, django_user_model):
        """已登录的非超级管理员访问批量切换接口时明确返回 403。"""
        normal = django_user_model.objects.create_user(username="norm3", password="x12345!")
        client.force_login(normal)
        resp = client.post(
            reverse("servers:update_user_groups", args=[ubuntu_server.pk]),
            {"username": "alice", "groups": "sudo"},
        )
        assert resp.status_code == 403

    def test_update_user_groups_no_change(self, client, ubuntu_server, django_user_model):
        """目标组与当前组一致：不调用 add/remove，只提示已更新。"""
        su = django_user_model.objects.create_user(username="su9", password="x12345!", is_staff=True, is_superuser=True)
        client.force_login(su)
        with (
            patch(
                "servers.views.get_user_groups_cached",
                return_value={"alice": ["nrm_managed"]},
            ),
            patch("servers.views.set_user_groups", return_value=(True, "无需变更")) as update,
        ):
            resp = client.post(
                reverse("servers:update_user_groups", args=[ubuntu_server.pk]),
                {"username": "alice", "groups": "nrm_managed"},
                follow=True,
            )
        assert resp.status_code == 200
        update.assert_called_once_with(
            ubuntu_server,
            "alice",
            {"nrm_managed"},
            {"nrm_managed"},
        )

    def test_detail_shows_user_groups(self, client, ubuntu_server, django_user_model):
        """详情页用统一标签编辑用户组，并区分现有组与受管标识组。"""
        su = django_user_model.objects.create_user(username="su5", password="x12345!", is_staff=True, is_superuser=True)
        owner = django_user_model.objects.create_user(
            username="alice-owner",
            first_name="张三丰",
            email="alice@example.com",
        )
        MachineUserBinding.objects.create(server=ubuntu_server, username="alice", user=owner)
        client.force_login(su)
        with (
            patch("servers.views.list_system_users", return_value=(True, [], "ok")),
            patch("servers.views.get_managed_users_cached", return_value=(["alice"], "ok")),
            patch(
                "servers.views.get_user_groups_cached",
                return_value={"alice": ["nrm_managed", "sudo", "docker"]},
            ),
        ):
            resp = client.get(reverse("servers:user_management", args=[ubuntu_server.pk]))
        html = resp.content.decode()
        assert "用户组" in html  # 列头
        # nrm_managed 与其他组使用同一标签结构，但有独立颜色且不可切换。
        assert 'class="btn btn-info btn-xs nrm-group-chip nrm-group-chip-managed"' in html
        assert 'data-group="nrm_managed"' in html
        # sudo/docker 渲染为现有组标签（data-active=1 表示提交时保留）。
        assert "btn btn-primary btn-xs nrm-group-chip nrm-group-chip-current group-toggle" in html
        assert 'data-group="sudo"' in html
        assert 'data-group="docker"' in html
        # 每行只保留统一确认按钮，组尾部用虚线添加入口，不常驻输入框。
        assert "data-save-groups" in html
        assert "确认组变更" in html
        assert "data-group-add" in html
        assert "data-group-add-confirm" in html
        assert "data-group-add-cancel" in html
        assert html.count("切换状态") == 1
        assert html.count("重置密码") == 1
        assert reverse("servers:reset_user_password", args=[ubuntu_server.pk]) in html
        assert "alice@example.com" in html
        assert "确定禁用用户" not in html
        assert 'name="group"' not in html

    def test_detail_shell_does_not_wait_for_ssh(self, client, ubuntu_server, django_user_model):
        admin = django_user_model.objects.create_superuser("async-admin", password="x12345!")
        client.force_login(admin)

        with patch("servers.views.list_system_users") as scan:
            response = client.get(reverse("servers:detail", args=[ubuntu_server.pk]))

        assert response.status_code == 200
        assert reverse("servers:user_management", args=[ubuntu_server.pk]) in response.content.decode()
        assert "正在异步读取用户状态" in response.content.decode()
        scan.assert_not_called()

    def test_reset_password_emails_bound_user(self, client, ubuntu_server, django_user_model):
        admin = django_user_model.objects.create_superuser("reset-admin", password="x12345!")
        owner = django_user_model.objects.create_user(
            username="owner",
            first_name="张三丰",
            email="owner@example.com",
        )
        MachineUserBinding.objects.create(server=ubuntu_server, username="alice", user=owner)
        application = Application.objects.create(
            applicant=owner,
            applicant_name="张三丰",
            username="alice",
            target_server=ubuntu_server,
            initial_password="OldPasswordMustExpire",
        )
        client.force_login(admin)

        with (
            patch(
                "servers.views.reset_user_password",
                return_value=(True, "TemporaryPass123", "用户 alice 的密码已重置"),
            ) as reset,
            patch("servers.views.send_machine_password_reset", return_value=True) as send,
        ):
            response = client.post(
                reverse("servers:reset_user_password", args=[ubuntu_server.pk]),
                {"username": "alice"},
                follow=True,
            )

        reset.assert_called_once_with(ubuntu_server, "alice")
        send.assert_called_once_with(owner, ubuntu_server, "alice", "TemporaryPass123")
        application.refresh_from_db()
        assert application.initial_password == ""
        assert "owner@example.com" in response.content.decode()

    def test_reset_password_requires_bound_user_email(self, client, ubuntu_server, django_user_model):
        admin = django_user_model.objects.create_superuser("reset-admin2", password="x12345!")
        owner = django_user_model.objects.create_user(username="owner2", email="")
        MachineUserBinding.objects.create(server=ubuntu_server, username="alice", user=owner)
        client.force_login(admin)

        with patch("servers.views.reset_user_password") as reset:
            response = client.post(
                reverse("servers:reset_user_password", args=[ubuntu_server.pk]),
                {"username": "alice"},
                follow=True,
            )

        reset.assert_not_called()
        assert "未配置邮箱" in response.content.decode()

    def test_reset_password_reports_email_partial_failure(self, client, ubuntu_server, django_user_model):
        admin = django_user_model.objects.create_superuser("reset-admin3", password="x12345!")
        owner = django_user_model.objects.create_user(username="owner3", email="owner3@example.com")
        MachineUserBinding.objects.create(server=ubuntu_server, username="alice", user=owner)
        client.force_login(admin)

        with (
            patch(
                "servers.views.reset_user_password",
                return_value=(True, "TemporaryPass123", "用户 alice 的密码已重置"),
            ),
            patch("servers.views.send_machine_password_reset", return_value=False),
        ):
            response = client.post(
                reverse("servers:reset_user_password", args=[ubuntu_server.pk]),
                {"username": "alice"},
                follow=True,
            )

        html = response.content.decode()
        assert "密码已重置" in html
        assert "邮件发送失败" in html
        assert "TemporaryPass123" not in html

    def test_reset_password_remote_failure_keeps_existing_credential(self, client, ubuntu_server, django_user_model):
        admin = django_user_model.objects.create_superuser("reset-admin4", password="x12345!")
        owner = django_user_model.objects.create_user(username="owner4", email="owner4@example.com")
        MachineUserBinding.objects.create(server=ubuntu_server, username="alice", user=owner)
        application = Application.objects.create(
            applicant=owner,
            applicant_name="owner4",
            username="alice",
            target_server=ubuntu_server,
            initial_password="StillValidPassword",
        )
        client.force_login(admin)

        with (
            patch("servers.views.reset_user_password", return_value=(False, "", "SSH 重置失败")),
            patch("servers.views.send_machine_password_reset") as send,
        ):
            response = client.post(
                reverse("servers:reset_user_password", args=[ubuntu_server.pk]),
                {"username": "alice"},
                follow=True,
            )

        application.refresh_from_db()
        assert application.initial_password == "StillValidPassword"
        send.assert_not_called()
        assert "SSH 重置失败" in response.content.decode()

    def test_reset_password_requires_superuser(self, client, ubuntu_server, django_user_model):
        staff = django_user_model.objects.create_user("reset-staff", is_staff=True)
        client.force_login(staff)

        with patch("servers.views.reset_user_password") as reset:
            response = client.post(
                reverse("servers:reset_user_password", args=[ubuntu_server.pk]),
                {"username": "alice"},
            )

        assert response.status_code == 403
        reset.assert_not_called()
