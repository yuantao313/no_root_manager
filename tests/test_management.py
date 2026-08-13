"""服务器管理服务测试：提权包装、接管、开通、目录迁移、sudo。"""

from unittest.mock import patch

import pytest
from django.urls import reverse

from credentials.models import Credential
from servers.management import (
    _random_password,
    _sudo_wrap,
    provision_user,
    take_over_user,
)
from servers.models import Server

pytestmark = pytest.mark.django_db


@pytest.fixture
def root_server():
    cred = Credential.objects.create(name="root", username="root", password="")
    return Server.objects.create(name="root机", host="10.0.0.1", port=22, credential=cred)


@pytest.fixture
def ubuntu_server():
    cred = Credential.objects.create(name="ubuntu", username="ubuntu", password="")
    return Server.objects.create(name="ubuntu机", host="10.0.0.2", port=22, credential=cred)


class TestRandomPassword:
    def test_length_and_alphabet(self):
        pwd = _random_password()
        assert len(pwd) == 16
        assert pwd.isalnum()

    def test_custom_length(self):
        assert len(_random_password(8)) == 8


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

        su = django_user_model.objects.create_user(
            username="su2", password="x12345!", is_staff=True, is_superuser=True
        )
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
            resp = client.get(reverse("servers:detail", args=[ubuntu_server.pk]))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "系统用户" in html  # 列头
        # 已绑定用户：显示平台用户名链接；未绑定用户：显示"未绑定"
        assert "normal2</a>" in html
        assert "未绑定" in html
        # 未绑定的机器用户也在列表中
        assert "m_user_c" in html


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

    def test_get_user_groups_cached(self, ubuntu_server):
        from servers.management import clear_user_groups_cache, get_user_groups_cached

        clear_user_groups_cache()
        out = "USER_GROUPS alice nrm_managed\nUSER_GROUPS bob nrm_managed\n"
        with patch("servers.management._run_mgmt", return_value=(True, out, "")) as mock:
            groups_map = get_user_groups_cached(ubuntu_server, ["alice", "bob"])
            assert groups_map["alice"] == ["nrm_managed"]
            # 二次查询命中缓存，不再 SSH
            groups_map2 = get_user_groups_cached(ubuntu_server, ["alice"])
        assert mock.call_count == 1
        assert groups_map2["alice"] == ["nrm_managed"]

    def test_add_group_view_requires_superuser(self, client, ubuntu_server, django_user_model):
        """非超级管理员访问加组接口 → 重定向登录（user_passes_test 默认行为）。"""
        normal = django_user_model.objects.create_user(username="norm2", password="x12345!")
        client.force_login(normal)
        resp = client.post(
            reverse("servers:add_user_group", args=[ubuntu_server.pk]),
            {"username": "alice", "group": "docker"},
        )
        assert resp.status_code == 302

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

    def test_detail_shows_user_groups(self, client, ubuntu_server, django_user_model):
        """详情页受管用户列表显示所属组（nrm_managed 无移除按钮，其他组有）。"""
        su = django_user_model.objects.create_user(username="su5", password="x12345!", is_staff=True, is_superuser=True)
        client.force_login(su)
        with (
            patch("servers.views.list_system_users", return_value=(True, [], "ok")),
            patch("servers.views.get_managed_users_cached", return_value=(["alice"], "ok")),
            patch(
                "servers.views.get_user_groups_cached",
                return_value={"alice": ["nrm_managed", "sudo", "docker"]},
            ),
        ):
            resp = client.get(reverse("servers:detail", args=[ubuntu_server.pk]))
        html = resp.content.decode()
        assert "用户组" in html  # 列头
        assert ">sudo</span>" in html
        assert ">docker</span>" in html
        assert "加组" in html  # 加组按钮
        # nrm_managed 不渲染移除按钮，sudo/docker 组有 × 移除表单
        assert 'value="nrm_managed"' not in html
        assert 'value="sudo"' in html
        assert 'value="docker"' in html
