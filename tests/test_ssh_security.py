"""SSH 主机身份、认证来源和临时脚本安全回归测试。"""

import base64
import shlex
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import paramiko
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from credentials.models import Credential
from servers import ssh
from servers.admin import ServerAdmin
from servers.forms import SERVER_EDIT_FIELDS, ServerForm
from servers.management import grant_sudo, usermod_add_group
from servers.models import Server

pytestmark = pytest.mark.django_db


def _fingerprint(byte: bytes = b"a") -> str:
    encoded = base64.b64encode(byte * 32).decode().rstrip("=")
    return f"SHA256:{encoded}"


@pytest.fixture
def credential():
    return Credential.objects.create(name="ssh-security", username="root", password="secret")


@pytest.fixture
def pinned_server(credential):
    return Server.objects.create(
        name="pinned",
        host="10.0.0.8",
        credential=credential,
        ssh_host_key_fingerprint=_fingerprint(),
    )


class TestHostKeyPinning:
    def test_normalize_openssh_sha256_fingerprint(self):
        padded = _fingerprint() + "="
        assert ssh.normalize_host_key_fingerprint(padded) == _fingerprint()

    @pytest.mark.parametrize("value", ["", "MD5:aa:bb", "SHA256:not-base64", "SHA256:YQ"])
    def test_rejects_invalid_or_weak_fingerprint(self, value):
        with pytest.raises(ValueError):
            ssh.normalize_host_key_fingerprint(value)

    def test_pinned_policy_accepts_exact_fingerprint(self):
        policy = ssh.PinnedHostKeyPolicy(_fingerprint())
        key = SimpleNamespace(fingerprint=_fingerprint())
        policy.missing_host_key(None, "host", key)

    def test_pinned_policy_rejects_changed_host_key(self):
        policy = ssh.PinnedHostKeyPolicy(_fingerprint(b"a"))
        key = SimpleNamespace(fingerprint=_fingerprint(b"b"))
        with pytest.raises(paramiko.SSHException, match="指纹不匹配"):
            policy.missing_host_key(None, "host", key)

    def test_unpinned_test_only_returns_candidate_for_out_of_band_confirmation(self):
        with patch("servers.ssh._scan_host_key", return_value=("ssh-ed25519", _fingerprint())) as scan:
            ok, message = ssh.test_connection("host", 22, "root", password="secret")

        assert ok is False
        assert _fingerprint() in message
        assert "可信渠道核对" in message
        scan.assert_called_once_with("host", 22, 8)

    def test_connect_uses_only_configured_credentials_and_all_timeouts(self):
        client = MagicMock()
        with patch("servers.ssh.paramiko.SSHClient", return_value=client):
            ok, _ = ssh.test_connection(
                "host",
                2222,
                "root",
                password="secret",
                host_key_fingerprint=_fingerprint(),
                timeout=7,
            )

        assert ok is True
        policy = client.set_missing_host_key_policy.call_args.args[0]
        assert isinstance(policy, ssh.PinnedHostKeyPolicy)
        kwargs = client.connect.call_args.kwargs
        assert kwargs["allow_agent"] is False
        assert kwargs["look_for_keys"] is False
        assert kwargs["timeout"] == 7
        assert kwargs["auth_timeout"] == 7
        assert kwargs["banner_timeout"] == 7
        assert kwargs["channel_timeout"] == 7

    def test_operational_connect_refuses_unpinned_server(self, credential):
        server = Server(name="unpinned", host="host", credential=credential)
        with (
            patch("servers.ssh.paramiko.SSHClient") as client_cls,
            pytest.raises(paramiko.SSHException, match="指纹未确认"),
        ):
            ssh._connect(server)
        client_cls.assert_not_called()

    def test_operational_connect_reuses_common_client_factory(self, pinned_server):
        client = MagicMock()
        with patch("servers.ssh._open_client", return_value=client) as open_client:
            assert ssh._connect(pinned_server, timeout=6) is client

        open_client.assert_called_once_with(
            host=pinned_server.host,
            port=pinned_server.port,
            username=pinned_server.credential.username,
            password=pinned_server.credential.password,
            private_key=pinned_server.credential.private_key,
            host_key_fingerprint=pinned_server.ssh_host_key_fingerprint,
            timeout=6,
        )


class TestServerFingerprintForm:
    def test_form_and_admin_share_editable_fields(self):
        assert ServerForm.Meta.fields == SERVER_EDIT_FIELDS
        assert ServerAdmin.fieldsets[0][1]["fields"] == SERVER_EDIT_FIELDS

    def _data(self, credential, **overrides):
        data = {
            "name": "server",
            "host": "10.0.0.8",
            "port": 22,
            "credential": credential.pk,
            "default_group": "",
            "ssh_host_key_fingerprint": "",
            "action": "save",
        }
        data.update(overrides)
        return data

    def test_normal_save_requires_confirmed_fingerprint(self, credential):
        form = ServerForm(self._data(credential))
        assert form.is_valid() is False
        assert "ssh_host_key_fingerprint" in form.errors

    def test_first_connection_test_may_scan_without_saving_fingerprint(self, credential):
        form = ServerForm(self._data(credential, action="test"))
        assert form.is_valid(), form.errors

    def test_valid_fingerprint_is_normalized(self, credential):
        form = ServerForm(
            self._data(
                credential,
                ssh_host_key_fingerprint=_fingerprint() + "=",
            )
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["ssh_host_key_fingerprint"] == _fingerprint()

    @pytest.mark.parametrize("group", ["sudo", "wheel", "docker", "dev,docker"])
    def test_default_group_rejects_root_equivalent_groups(self, credential, group):
        form = ServerForm(
            self._data(
                credential,
                ssh_host_key_fingerprint=_fingerprint(),
                default_group=group,
            )
        )

        assert not form.is_valid()
        assert "root 级权限组" in form.errors["default_group"][0]

    @pytest.mark.parametrize("group", ["bad group", "-ops", "a" * 33])
    def test_default_group_rejects_invalid_linux_group_names(self, credential, group):
        form = ServerForm(
            self._data(
                credential,
                ssh_host_key_fingerprint=_fingerprint(),
                default_group=group,
            )
        )

        assert not form.is_valid()
        assert "用户组名称不合法" in form.errors["default_group"][0]

    def test_default_group_accepts_uppercase_linux_group_names(self, credential):
        form = ServerForm(
            self._data(
                credential,
                ssh_host_key_fingerprint=_fingerprint(),
                default_group="DevOps,AI_Group",
            )
        )

        assert form.is_valid(), form.errors
        assert form.cleaned_data["default_group"] == "DevOps,AI_Group"

    def test_default_group_is_trimmed_and_deduplicated(self, credential):
        form = ServerForm(
            self._data(
                credential,
                ssh_host_key_fingerprint=_fingerprint(),
                default_group=" dev,ops,dev ",
            )
        )

        assert form.is_valid(), form.errors
        assert form.cleaned_data["default_group"] == "dev,ops"


class TestServerFormViews:
    def _data(self, credential, **overrides):
        data = {
            "name": "server",
            "host": "10.0.0.8",
            "port": 22,
            "credential": credential.pk,
            "default_group": "dev",
            "ssh_host_key_fingerprint": _fingerprint(),
            "action": "save",
        }
        data.update(overrides)
        return data

    def test_create_and_edit_use_same_save_flow(self, client, credential):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "x12345!")
        client.force_login(admin)

        created = client.post(reverse("servers:create"), self._data(credential))
        server = Server.objects.get(name="server")
        updated = client.post(reverse("servers:edit", args=[server.pk]), self._data(credential, name="renamed"))

        assert created.status_code == 302
        assert updated.status_code == 302
        server.refresh_from_db()
        assert server.name == "renamed"

    def test_create_page_renders_all_core_fields(self, client, credential):
        admin = get_user_model().objects.create_superuser("form-admin", "admin@example.com", "x12345!")
        client.force_login(admin)

        response = client.get(reverse("servers:create"))

        html = response.content.decode()
        assert response.status_code == 200
        for field_name in ServerForm.Meta.fields:
            assert f'id="id_{field_name}"' in html
        assert credential.name in html
        assert "获取指纹 / 测试并保存" in html
        assert "新账号默认用户组" in html
        assert f"next={reverse('servers:create')}" in html
        assert 'class="nrm-form-grid"' in html
        assert html.count("nrm-form-row") >= len(ServerForm.Meta.fields)

    def test_failed_connection_test_does_not_save(self, client, credential):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "x12345!")
        client.force_login(admin)
        with patch("servers.views.test_server_connection", return_value=(False, "fingerprint mismatch")):
            response = client.post(reverse("servers:create"), self._data(credential, action="test"))

        assert response.status_code == 200
        assert not Server.objects.filter(name="server").exists()

    def test_first_probe_exposes_fillable_candidate_without_auto_saving(self, client, credential):
        admin = get_user_model().objects.create_superuser("candidate-admin", "admin@example.com", "x12345!")
        client.force_login(admin)
        candidate = _fingerprint()
        message = f"SSH 主机身份尚未确认：算法 ssh-ed25519，指纹 {candidate}。请通过可信渠道核对。"
        with patch("servers.views.test_server_connection", return_value=(False, message)):
            response = client.post(
                reverse("servers:create"),
                self._data(credential, action="test", ssh_host_key_fingerprint=""),
            )

        html = response.content.decode()
        assert response.status_code == 200
        assert candidate in html
        assert f'data-fill-host-key="{candidate}"' in html
        assert not Server.objects.filter(name="server").exists()

    @pytest.mark.parametrize(
        ("route", "handler", "payload"),
        [
            ("toggle_user_lock", "servers.views.toggle_user_lock", {"username": "alice"}),
            ("add_user_group", "servers.views.add_user_group", {"username": "alice", "group": "dev"}),
            ("remove_user_group", "servers.views.remove_user_group", {"username": "alice", "group": "dev"}),
        ],
    )
    def test_user_action_routes_keep_existing_contract(self, client, credential, route, handler, payload):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "x12345!")
        server = Server.objects.create(
            name="server", host="10.0.0.8", credential=credential, ssh_host_key_fingerprint=_fingerprint()
        )
        client.force_login(admin)
        with patch(handler, return_value=(True, "ok")) as operation:
            response = client.post(reverse(f"servers:{route}", args=[server.pk]), payload)

        assert response.status_code == 302
        operation.assert_called_once()


class TestServerCredentialTabs:
    def test_superuser_manages_servers_and_credentials_from_one_entry(self, client, credential):
        admin = get_user_model().objects.create_superuser("tabs-admin", "admin@example.com", "x12345!")
        Server.objects.create(
            name="tab-server",
            host="10.0.0.9",
            credential=credential,
            ssh_host_key_fingerprint=_fingerprint(),
        )
        client.force_login(admin)

        server_html = client.get(reverse("servers:list")).content.decode()
        credential_html = client.get(reverse("servers:list"), {"tab": "credentials"}).content.decode()

        assert "服务器与凭据" in server_html
        assert "新增服务器" in server_html
        assert "tab-server" in server_html
        assert "新增凭据" in credential_html
        assert credential.name in credential_html
        assert "1 台" in credential_html
        assert credential.password not in credential_html

    def test_normal_admin_cannot_open_combined_management(self, client):
        admin = get_user_model().objects.create_user("limited-admin", password="x12345!", is_staff=True)
        client.force_login(admin)

        assert client.get(reverse("servers:list")).status_code == 403

    def test_server_table_is_paginated(self, client, credential):
        admin = get_user_model().objects.create_superuser("page-admin", "admin@example.com", "x12345!")
        for index in range(21):
            Server.objects.create(
                name=f"server-{index:02d}",
                host=f"10.0.1.{index + 1}",
                credential=credential,
                ssh_host_key_fingerprint=_fingerprint(),
            )
        client.force_login(admin)

        response = client.get(reverse("servers:list"), {"tab": "servers", "page": 2})

        assert response.status_code == 200
        assert response.context["page_obj"].paginator.count == 21
        assert len(response.context["servers"]) == 1
        assert "第 2 / 2 页，共 21 条" in response.content.decode()

    def test_detail_uses_post_forms_for_remote_actions(self, client, credential):
        admin = get_user_model().objects.create_superuser("actions-admin", "admin@example.com", "x12345!")
        server = Server.objects.create(
            name="actions-server",
            host="10.0.0.9",
            credential=credential,
            default_group="dev,ops",
            ssh_host_key_fingerprint=_fingerprint(),
        )
        client.force_login(admin)
        with (
            patch("servers.views.list_system_users", return_value=(True, [], "ok")),
            patch("servers.views.get_managed_users_cached", return_value=([], "ok")),
        ):
            response = client.get(reverse("servers:detail", args=[server.pk]))

        html = response.content.decode()
        user_html = client.get(reverse("servers:user_management", args=[server.pk])).content.decode()
        assert "新账号默认分组" in html
        assert "dev,ops" in html
        assert "nrm-info-grid" in html
        assert "info-table" not in html
        assert f'action="{reverse("servers:test", args=[server.pk])}"' in html
        assert f'action="{reverse("servers:sync_users", args=[server.pk])}"' in user_html
        assert client.get(reverse("servers:test", args=[server.pk])).status_code == 405
        assert client.get(reverse("servers:sync_users", args=[server.pk])).status_code == 405


class _Stream:
    def __init__(self, value=b"", status=0):
        self.value = value
        self.channel = SimpleNamespace(recv_exit_status=lambda: status)
        self.written = ""
        self.closed = False

    def read(self):
        return self.value

    def write(self, value):
        self.written += value

    def close(self):
        self.closed = True


class TestRemoteScriptLifecycle:
    def test_command_result_preserves_output_and_exit_message(self):
        stdout = _Stream(b"partial", status=9)
        stderr = _Stream()

        assert ssh._read_command_result(stdout, stderr, "命令") == (False, "partial", "命令退出码 9")

    def test_upload_uses_random_private_directory(self):
        sftp = MagicMock()
        client = MagicMock()
        client.open_sftp.return_value = sftp
        with patch("servers.ssh.secrets.token_hex", return_value="fixed-random"):
            path = ssh._upload_script(client, "/repo/script.sh", "script.sh")

        assert path == "/tmp/nrm-fixed-random/script.sh"
        sftp.mkdir.assert_called_once_with("/tmp/nrm-fixed-random", mode=0o700)
        assert sftp.chmod.call_args_list[0].args == ("/tmp/nrm-fixed-random", 0o700)
        assert sftp.chmod.call_args_list[1].args == (path, 0o700)
        sftp.put.assert_called_once_with("/repo/script.sh", path)
        sftp.close.assert_called_once()

    @pytest.mark.parametrize(
        ("username", "command_prefix"),
        [("root", "bash "), ("ubuntu", "sudo -n bash ")],
    )
    def test_run_script_uses_minimal_privilege_and_always_cleans_up(self, pinned_server, username, command_prefix):
        pinned_server.credential.username = username
        client = MagicMock()
        stdin = _Stream()
        stdout = _Stream(b"done", status=0)
        stderr = _Stream()
        client.exec_command.return_value = (stdin, stdout, stderr)
        remote_path = "/tmp/nrm-random/script.sh"

        with (
            patch("servers.ssh._connect", return_value=client),
            patch("servers.ssh._upload_script", return_value=remote_path),
            patch("servers.ssh._cleanup_remote_script") as cleanup,
        ):
            ok, out, err = ssh.run_script(
                pinned_server,
                "/repo/script.sh",
                args=["arg with space"],
                stdin_data="secret-input",
            )

        assert (ok, out, err) == (True, "done", "")
        command = client.exec_command.call_args.args[0]
        assert command == f"{command_prefix}{shlex.join([remote_path, 'arg with space'])}"
        assert stdin.written == "secret-input\n"
        cleanup.assert_called_once_with(client, remote_path)
        client.close.assert_called_once()

    def test_run_script_cleans_up_after_execution_error(self, pinned_server):
        client = MagicMock()
        client.exec_command.side_effect = RuntimeError("channel failed")
        remote_path = "/tmp/nrm-random/script.sh"
        with (
            patch("servers.ssh._connect", return_value=client),
            patch("servers.ssh._upload_script", return_value=remote_path),
            patch("servers.ssh._cleanup_remote_script") as cleanup,
        ):
            ok, _, message = ssh.run_script(pinned_server, "/repo/script.sh")

        assert ok is False
        assert "channel failed" in message
        cleanup.assert_called_once_with(client, remote_path)
        client.close.assert_called_once()


class TestGrantSudoResult:
    def test_reports_actual_wheel_group(self, pinned_server):
        with patch(
            "servers.management._run_mgmt",
            return_value=(True, "OK grant_sudo alice group=wheel", ""),
        ):
            ok, group, message = grant_sudo(pinned_server, "alice")
        assert ok is True
        assert group == "wheel"
        assert "wheel" in message

    def test_rejects_success_without_confirmed_sudo_group(self, pinned_server):
        with patch(
            "servers.management._run_mgmt",
            return_value=(True, "OK grant_sudo alice group=none", ""),
        ):
            ok, group, message = grant_sudo(pinned_server, "alice")
        assert ok is False
        assert group == ""
        assert "无法确认" in message

    def test_group_request_sudo_uses_platform_detection(self, pinned_server):
        with patch("servers.management.grant_sudo", return_value=(True, "wheel", "granted")) as grant:
            assert usermod_add_group(pinned_server, "alice", "sudo") == (True, "granted")
        grant.assert_called_once_with(pinned_server, "alice")

    def test_group_request_docker_never_uses_group_creating_command(self, pinned_server):
        with patch(
            "servers.management._run_mgmt",
            return_value=(True, "OK grant_docker alice group=docker", ""),
        ) as run:
            ok, _ = usermod_add_group(pinned_server, "alice", "docker")

        assert ok is True
        run.assert_called_once_with(pinned_server, ["grant_docker", "alice"])
