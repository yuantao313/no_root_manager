"""服务器管理服务测试：提权包装、接管、开通、目录迁移、sudo。"""

from unittest.mock import patch

import pytest

from credentials.models import Credential
from servers.management import (
    _random_password,
    _sudo_wrap,
    apply_resource_limits,
    migrate_home_dir,
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


class TestMigrateHomeDir:
    def test_empty_source(self, ubuntu_server):
        ok, msg = migrate_home_dir(ubuntu_server, "", "u")
        assert ok is False and "未指定" in msg

    def test_relative_path_rejected(self, ubuntu_server):
        ok, msg = migrate_home_dir(ubuntu_server, "home/old/u", "u")
        assert ok is False and "绝对路径" in msg

    def test_injection_chars_rejected(self, ubuntu_server):
        ok, msg = migrate_home_dir(ubuntu_server, "/home/old/u; rm -rf /", "u")
        assert ok is False and "非法字符" in msg

    def test_rollback_on_chown_failure(self, ubuntu_server):
        commands = []

        def fake_exec(server, cmd):
            commands.append(cmd)
            if cmd.startswith("test -d /home/u"):
                return True, "", ""
            if cmd.startswith("sudo -n sh -c"):
                return True, "0", ""
            if "mv -T" in cmd:
                return True, "", ""
            if "chown" in cmd:
                return False, "", "denied"
            return True, "", ""

        with patch("servers.management._exec", side_effect=fake_exec):
            ok, msg = migrate_home_dir(ubuntu_server, "/home/old/u", "u")
        assert ok is False
        assert "已回滚" in msg
        assert any("mv -T /home/u /home/old/u" in c for c in commands)

    def test_success_no_rollback(self, ubuntu_server):
        commands = []

        def fake_exec(server, cmd):
            commands.append(cmd)
            if cmd.startswith("test -d /home/u"):
                return True, "", ""
            if cmd.startswith("sudo -n sh -c"):
                return True, "0", ""
            if "mv -T" in cmd or "chown" in cmd or "rmdir" in cmd:
                return True, "", ""
            return True, "", ""

        with patch("servers.management._exec", side_effect=fake_exec):
            ok, msg = migrate_home_dir(ubuntu_server, "/home/old/u", "u")
        assert ok is True
        assert not any("mv -T /home/u /home/old/u" in c for c in commands)


class TestTakeOver:
    def test_empty_username(self, ubuntu_server):
        ok, msg = take_over_user(ubuntu_server, "")
        assert ok is False and "用户名为空" in msg

    def test_takeover_uses_privileged_cmd(self, ubuntu_server):
        with patch("servers.management._exec", return_value=(True, "", "")) as mock:
            ok, _ = take_over_user(ubuntu_server, "alice")
        assert ok is True
        cmd = mock.call_args.args[1]
        # _exec 内部会做 _sudo_wrap 提权包装（由 TestSudoWrap 覆盖），这里断言原始特权命令
        assert "usermod -aG nrm_managed alice" in cmd


class TestProvisionUser:
    def test_empty_username(self, ubuntu_server):
        ok, pwd, msg = provision_user(ubuntu_server, "")
        assert ok is False and "用户名为空" in msg

    def test_existing_user_skips_create(self, ubuntu_server):
        commands = []

        def fake_exec(server, cmd):
            commands.append(cmd)
            if cmd.startswith("id -u"):
                return True, "1000", ""  # 用户已存在
            return True, "", ""

        with patch("servers.management._exec", side_effect=fake_exec):
            ok, pwd, msg = provision_user(ubuntu_server, "bob")
        assert ok is True
        assert len(pwd) == 16
        # 已存在用户不应执行 useradd
        assert not any(c.startswith("useradd") for c in commands)
        assert any("usermod -aG" in c for c in commands)

    def test_new_user_created_with_groups(self, ubuntu_server):
        commands = []

        def fake_exec(server, cmd):
            commands.append(cmd)
            if cmd.startswith("id -u"):
                return False, "", ""  # 用户不存在
            return True, "", ""

        with patch("servers.management._exec", side_effect=fake_exec):
            ok, pwd, msg = provision_user(ubuntu_server, "carol", groups=["dev"])
        assert ok is True
        assert "dev,nrm_managed" in msg
        assert any("useradd" in c and "dev,nrm_managed" in c for c in commands)
        # 默认强制首次改密
        assert any("chage -d 0" in c for c in commands)

    def test_without_home_flag(self, ubuntu_server):
        commands = []

        def fake_exec(server, cmd):
            commands.append(cmd)
            if cmd.startswith("id -u"):
                return False, "", ""
            return True, "", ""

        with patch("servers.management._exec", side_effect=fake_exec):
            provision_user(ubuntu_server, "dave", with_home=False)
        create_cmd = next(c for c in commands if c.startswith("useradd"))
        assert "-m" not in create_cmd

    def test_force_pwd_change_off(self, ubuntu_server):
        commands = []

        def fake_exec(server, cmd):
            commands.append(cmd)
            if cmd.startswith("id -u"):
                return False, "", ""
            return True, "", ""

        with patch("servers.management._exec", side_effect=fake_exec):
            provision_user(ubuntu_server, "erin", force_pwd_change=False)
        assert not any("chage -d 0" in c for c in commands)


class TestApplyResourceLimits:
    def test_empty_username(self, ubuntu_server):
        ok, msg = apply_resource_limits(ubuntu_server, "")
        assert ok is False and "用户名为空" in msg

    def test_no_limits_configured(self, ubuntu_server):
        ubuntu_server.nproc_limit = 0
        ubuntu_server.nofile_limit = 0
        ubuntu_server.save()
        with patch("servers.management._exec") as mock:
            ok, msg = apply_resource_limits(ubuntu_server, "alice")
        assert ok is True
        assert "未配置" in msg
        mock.assert_not_called()

    def test_writes_limits_conf(self, ubuntu_server):
        with patch("servers.management._exec", return_value=(True, "", "")) as mock:
            ok, msg = apply_resource_limits(ubuntu_server, "alice")
        assert ok is True
        cmd = mock.call_args.args[1]
        assert "limits.d/nrm-alice.conf" in cmd
        assert "alice hard nproc 128" in cmd
        assert "alice hard nofile 2048" in cmd

    def test_zero_limit_skips_line(self, ubuntu_server):
        ubuntu_server.nproc_limit = 0
        ubuntu_server.save()
        with patch("servers.management._exec", return_value=(True, "", "")) as mock:
            apply_resource_limits(ubuntu_server, "alice")
        cmd = mock.call_args.args[1]
        assert "nproc" not in cmd
        assert "nofile" in cmd

    def test_provision_writes_limits(self, ubuntu_server):
        commands = []

        def fake_exec(server, cmd):
            commands.append(cmd)
            if cmd.startswith("id -u"):
                return False, "", ""
            return True, "", ""

        with patch("servers.management._exec", side_effect=fake_exec):
            ok, pwd, msg = provision_user(ubuntu_server, "gina")
        assert ok is True
        assert any("limits.d/nrm-gina.conf" in c for c in commands)

    def test_writes_all_limit_items(self, ubuntu_server):
        ubuntu_server.nproc_limit = 128
        ubuntu_server.nofile_limit = 2048
        ubuntu_server.as_limit = 1048576
        ubuntu_server.core_limit = 0
        ubuntu_server.fsize_limit = 0
        ubuntu_server.maxlogins_limit = 3
        ubuntu_server.save()
        with patch("servers.management._exec", return_value=(True, "", "")) as mock:
            ok, msg = apply_resource_limits(ubuntu_server, "alice")
        assert ok is True
        cmd = mock.call_args.args[1]
        assert "alice hard nproc 128" in cmd
        assert "alice hard nofile 2048" in cmd
        assert "alice hard as 1048576" in cmd
        assert "alice hard maxlogins 3" in cmd
        # 0 值项不写入
        assert "hard core" not in cmd
        assert "hard fsize" not in cmd

    def test_zero_only_server_no_limits(self, ubuntu_server):
        ubuntu_server.nproc_limit = 0
        ubuntu_server.nofile_limit = 0
        ubuntu_server.as_limit = 0
        ubuntu_server.core_limit = 0
        ubuntu_server.fsize_limit = 0
        ubuntu_server.maxlogins_limit = 0
        ubuntu_server.save()
        with patch("servers.management._exec") as mock:
            ok, msg = apply_resource_limits(ubuntu_server, "alice")
        assert ok is True
        assert "未配置" in msg
        mock.assert_not_called()
