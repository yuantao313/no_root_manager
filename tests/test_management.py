"""服务器管理服务测试：提权包装、接管、开通、目录迁移、sudo。"""

from unittest.mock import patch

import pytest

from credentials.models import Credential
from servers.management import (
    _random_password,
    _sudo_wrap,
    apply_resource_limits,
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
            side_effect=[(True, "OK provision carol", ""), (True, "OK set_limits carol", "")],
        ) as mock:
            ok, pwd, msg = provision_user(ubuntu_server, "carol", groups=["dev"])
        assert ok is True
        assert len(pwd) == 16
        # 第一次调用是 provision（后续 set_limits 是资源限制）
        args = mock.call_args_list[0].args[1]
        # provision <user> <groups_csv> <expire> <with_home> <force_pwd>
        assert args[0] == "provision"
        assert args[1] == "carol"
        assert "dev,nrm_managed" in args[2]
        assert args[3] == "-"  # 无到期时间
        assert args[4] == "1"  # with_home
        assert args[5] == "1"  # force_pwd_change
        # 密码经 stdin 传递（不进命令行参数）
        stdin_data = mock.call_args_list[0].kwargs.get("stdin_data")
        assert stdin_data and stdin_data.startswith("carol:")

    def test_without_home_flag(self, ubuntu_server):
        with patch(
            "servers.management._run_mgmt",
            side_effect=[(True, "OK provision dave", ""), (True, "OK set_limits dave", "")],
        ) as mock:
            provision_user(ubuntu_server, "dave", with_home=False)
        args = mock.call_args_list[0].args[1]
        assert args[4] == "0"  # with_home=0

    def test_force_pwd_change_off(self, ubuntu_server):
        with patch(
            "servers.management._run_mgmt",
            side_effect=[(True, "OK provision erin", ""), (True, "OK set_limits erin", "")],
        ) as mock:
            provision_user(ubuntu_server, "erin", force_pwd_change=False)
        args = mock.call_args_list[0].args[1]
        assert args[5] == "0"  # force_pwd_change=0

    def test_expire_date_passed(self, ubuntu_server):
        with patch(
            "servers.management._run_mgmt",
            side_effect=[(True, "OK provision frank", ""), (True, "OK set_limits frank", "")],
        ) as mock:
            provision_user(ubuntu_server, "frank", expire_date="2026-12-31")
        args = mock.call_args_list[0].args[1]
        assert args[3] == "2026-12-31"


class TestApplyResourceLimits:
    def test_empty_username(self, ubuntu_server):
        ok, msg = apply_resource_limits(ubuntu_server, "")
        assert ok is False and "用户名为空" in msg

    def test_no_limits_configured(self, ubuntu_server):
        ubuntu_server.nproc_limit = 0
        ubuntu_server.nofile_limit = 0
        ubuntu_server.save()
        with patch("servers.management._run_mgmt") as mock:
            ok, msg = apply_resource_limits(ubuntu_server, "alice")
        assert ok is True
        assert "未配置" in msg
        mock.assert_not_called()

    def test_writes_limits_via_script(self, ubuntu_server):
        with patch("servers.management._run_mgmt", return_value=(True, "OK set_limits alice", "")) as mock:
            ok, msg = apply_resource_limits(ubuntu_server, "alice")
        assert ok is True
        args = mock.call_args.args[1]
        # set_limits <user> <item=value>...
        assert args[0] == "set_limits"
        assert args[1] == "alice"
        assert "nproc=128" in args
        assert "nofile=2048" in args

    def test_zero_limit_skips_line(self, ubuntu_server):
        ubuntu_server.nproc_limit = 0
        ubuntu_server.save()
        with patch("servers.management._run_mgmt", return_value=(True, "OK set_limits alice", "")) as mock:
            apply_resource_limits(ubuntu_server, "alice")
        args = mock.call_args.args[1]
        assert not any(a.startswith("nproc=") for a in args)
        assert any(a.startswith("nofile=") for a in args)

    def test_provision_writes_limits(self, ubuntu_server):
        """开通后自动调用 set_limits 写入资源限制。"""
        with patch(
            "servers.management._run_mgmt",
            side_effect=[(True, "OK provision gina", ""), (True, "OK set_limits gina", "")],
        ) as mock:
            ok, pwd, msg = provision_user(ubuntu_server, "gina")
        assert ok is True
        assert mock.call_count == 2
        assert mock.call_args_list[1].args[1][0] == "set_limits"

    def test_writes_all_limit_items(self, ubuntu_server):
        ubuntu_server.nproc_limit = 128
        ubuntu_server.nofile_limit = 2048
        ubuntu_server.as_limit = 1048576
        ubuntu_server.core_limit = 0
        ubuntu_server.fsize_limit = 0
        ubuntu_server.maxlogins_limit = 3
        ubuntu_server.save()
        with patch("servers.management._run_mgmt", return_value=(True, "OK set_limits alice", "")) as mock:
            ok, msg = apply_resource_limits(ubuntu_server, "alice")
        assert ok is True
        args = mock.call_args.args[1]
        assert "nproc=128" in args
        assert "nofile=2048" in args
        assert "as=1048576" in args
        assert "maxlogins=3" in args
        # 0 值项不写入
        assert not any(a.startswith("core=") for a in args)
        assert not any(a.startswith("fsize=") for a in args)

    def test_zero_only_server_no_limits(self, ubuntu_server):
        ubuntu_server.nproc_limit = 0
        ubuntu_server.nofile_limit = 0
        ubuntu_server.as_limit = 0
        ubuntu_server.core_limit = 0
        ubuntu_server.fsize_limit = 0
        ubuntu_server.maxlogins_limit = 0
        ubuntu_server.save()
        with patch("servers.management._run_mgmt") as mock:
            ok, msg = apply_resource_limits(ubuntu_server, "alice")
        assert ok is True
        assert "未配置" in msg
        mock.assert_not_called()
