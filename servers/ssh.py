"""SSH 连接与命令执行工具。"""

import base64
import binascii
import hmac
import io
import logging
import os
import posixpath
import secrets
import shlex
import socket

import paramiko

logger = logging.getLogger(__name__)


def normalize_host_key_fingerprint(value: str) -> str:
    """校验并规范化 OpenSSH SHA256 主机指纹。

    只接受 SHA256，避免在新配置中继续使用弱 MD5 指纹。
    """
    raw = (value or "").strip()
    try:
        algorithm, encoded = raw.split(":", 1)
    except ValueError as exc:
        raise ValueError("指纹必须为 OpenSSH SHA256:<base64> 格式。") from exc
    if algorithm.upper() != "SHA256":
        raise ValueError("仅支持 OpenSSH SHA256 主机指纹。")
    encoded = encoded.rstrip("=")
    try:
        digest = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("SSH 主机指纹的 base64 内容无效。") from exc
    if len(digest) != 32:
        raise ValueError("SSH 主机指纹必须是 32 字节 SHA256 摘要。")
    return f"SHA256:{encoded}"


class PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """仅允许与服务器配置中已确认指纹完全一致的主机密钥。"""

    def __init__(self, expected_fingerprint: str):
        self.expected_fingerprint = normalize_host_key_fingerprint(expected_fingerprint)

    def missing_host_key(self, client, hostname, key):  # noqa: ARG002
        actual = normalize_host_key_fingerprint(key.fingerprint)
        if not hmac.compare_digest(actual, self.expected_fingerprint):
            raise paramiko.SSHException(
                f"SSH 主机指纹不匹配：已确认 {self.expected_fingerprint}，实际 {actual}。请停止操作并核对目标机器身份。"
            )


def _scan_host_key(host: str, port: int, timeout: int = 8) -> tuple[str, str]:
    """只读获取远端主机密钥，供管理员通过可信渠道核对。"""
    sock = socket.create_connection((host, port), timeout=timeout)
    transport = None
    try:
        transport = paramiko.Transport(sock)
        transport.banner_timeout = timeout
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        return key.get_name(), normalize_host_key_fingerprint(key.fingerprint)
    finally:
        if transport is not None:
            transport.close()
        else:
            sock.close()


def _connect_kwargs(timeout: int) -> dict:
    """所有 SSH 入口共用的认证和超时策略。"""
    return {
        "timeout": timeout,
        "auth_timeout": timeout,
        "banner_timeout": timeout,
        "channel_timeout": timeout,
        # 凭据只能来自 NRM 已加密保存的配置，不借用运行机的 agent/~/.ssh。
        "allow_agent": False,
        "look_for_keys": False,
    }


def _load_key(private_key: str, password: str | None = None):
    """按 RSA / Ed25519 / ECDSA 顺序尝试解析私钥，密码作为密钥口令。"""
    errors = []
    for key_cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return key_cls.from_private_key(io.StringIO(private_key), password=password)
        except paramiko.SSHException as e:
            errors.append(f"{key_cls.__name__}: {e}")
    raise paramiko.SSHException("无法解析私钥：" + "；".join(errors))


def _open_client(*, host, port, username, password, private_key, host_key_fingerprint, timeout):
    """按统一的主机密钥、认证来源和超时策略建立 SSH 客户端。"""
    host_key_policy = PinnedHostKeyPolicy(host_key_fingerprint)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(host_key_policy)
    try:
        auth = {"pkey": _load_key(private_key, password)} if private_key else {"password": password}
        client.connect(
            host,
            port=port,
            username=username,
            **auth,
            **_connect_kwargs(timeout),
        )
    except Exception:
        client.close()
        raise
    return client


def test_connection(
    host,
    port,
    username,
    password=None,
    private_key=None,
    host_key_fingerprint="",
    timeout=8,
):
    """尝试连接目标服务器，返回 (是否成功, 信息)。"""
    if not host_key_fingerprint:
        try:
            algorithm, fingerprint = _scan_host_key(host, port, timeout)
        except Exception as e:  # noqa: BLE001 —— 测试连接需要展示所有失败原因
            return False, f"连接失败：{e}"
        return (
            False,
            "SSH 主机身份尚未确认："
            f"算法 {algorithm}，指纹 {fingerprint}。"
            "请通过机器控制台或运维人员等可信渠道核对，填入后重新测试。",
        )

    try:
        client = _open_client(
            host=host,
            port=port,
            username=username,
            password=password,
            private_key=private_key,
            host_key_fingerprint=host_key_fingerprint,
            timeout=timeout,
        )
    except ValueError as e:
        return False, f"SSH 主机指纹无效：{e}"
    except Exception as e:  # noqa: BLE001 —— 测试连接需要兜底展示所有失败原因
        return False, f"连接失败：{e}"
    client.close()
    return True, f"SSH 连接成功（{username}@{host}:{port}）"


def test_server_connection(server):
    """对 Server 实例执行连接测试。"""
    cred = server.credential
    return test_connection(
        server.host,
        server.port,
        cred.username,
        password=cred.password,
        private_key=cred.private_key,
        host_key_fingerprint=server.ssh_host_key_fingerprint,
    )


def _connect(server, timeout=8):
    """建立 SSH 连接，返回 client（调用方负责 close）。"""
    cred = server.credential
    if not server.ssh_host_key_fingerprint:
        raise paramiko.SSHException("SSH 主机指纹未确认：请在服务器编辑页先获取、核对并保存 SHA256 指纹。")
    return _open_client(
        host=server.host,
        port=server.port,
        username=cred.username,
        password=cred.password,
        private_key=cred.private_key,
        host_key_fingerprint=server.ssh_host_key_fingerprint,
        timeout=timeout,
    )


def _read_command_result(stdout, stderr, operation):
    """统一解码远程输出并把非零退出码转换为三元组结果。"""
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        return False, out, err or f"{operation}退出码 {exit_status}"
    return True, out, err


def exec_command(server, command, timeout=10):
    """在目标服务器执行命令，返回 (ok, stdout, stderr)。连接失败或命令非零退出均返回 False。"""
    try:
        client = _connect(server)
    except Exception as e:  # noqa: BLE001 —— 连接失败需兜底返回原因
        return False, "", f"连接失败：{e}"
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        return _read_command_result(stdout, stderr, "命令")
    except Exception as e:  # noqa: BLE001 —— 需兜底返回失败原因
        return False, "", str(e)
    finally:
        client.close()


SCRIPT_REMOTE_BASE = "/tmp"


def _upload_script(client, local_script: str, remote_name: str) -> str:
    """将脚本上传到随机且仅 SSH 用户可访问的远端目录。"""
    sftp = client.open_sftp()
    remote_dir = f"{SCRIPT_REMOTE_BASE}/nrm-{secrets.token_hex(16)}"
    remote_path = f"{remote_dir}/{remote_name}"
    try:
        # mkdir 具有排他性；128 bit 随机名防止 /tmp 中的预创建/替换。
        sftp.mkdir(remote_dir, mode=0o700)
        sftp.chmod(remote_dir, 0o700)
        sftp.put(local_script, remote_path)
        sftp.chmod(remote_path, 0o700)
        return remote_path
    except Exception:
        # 上传中途失败也不保留可能包含管理逻辑的脚本。
        try:
            sftp.remove(remote_path)
        except Exception:  # noqa: BLE001 —— 原始上传异常优先
            pass
        try:
            sftp.rmdir(remote_dir)
        except Exception:  # noqa: BLE001 —— 原始上传异常优先
            pass
        raise
    finally:
        sftp.close()


def _cleanup_remote_script(client, remote_path: str) -> None:
    """尽力删除已上传脚本及其随机目录，不覆盖主操作结果。"""
    remote_dir = posixpath.dirname(remote_path)
    try:
        sftp = client.open_sftp()
    except Exception:  # noqa: BLE001 —— SSH 已断开时无法清理
        logger.warning("无法打开 SFTP 清理远端脚本：%s", remote_path, exc_info=True)
        return
    try:
        try:
            sftp.remove(remote_path)
        except Exception:  # noqa: BLE001 —— 清理失败不能改写脚本执行结果
            logger.warning("无法删除远端脚本：%s", remote_path, exc_info=True)
        try:
            sftp.rmdir(remote_dir)
        except Exception:  # noqa: BLE001 —— 清理失败不能改写脚本执行结果
            logger.warning("无法删除远端脚本目录：%s", remote_dir, exc_info=True)
    finally:
        sftp.close()


def run_script(
    server,
    local_script: str,
    args: list[str] | None = None,
    timeout=30,
    stdin_data: str | None = None,
    connect_timeout=8,
):
    """将本地脚本上传到目标机并以 root 权限执行，返回 (ok, stdout, stderr)。

    - local_script：代码库内的脚本文件路径（如 servers/scripts/nrm_mgmt.sh）
    - args：传给脚本的子命令参数
    - stdin_data：可选，写入脚本 stdin（如 provision 的 user:password）
    - connect_timeout：SSH 连接超时（秒）。页面渲染路径上的查询（如设备信息）
      应传短超时，避免目标机不可达时拖死页面
    - SSH 用户为 root 时直接执行 `bash`，其他用户才使用 `sudo -n bash`
    - 脚本置于 0700 随机临时目录，并在 finally 中清理
    """
    args = args or []
    script_name = os.path.basename(local_script)
    remote_path = ""
    try:
        client = _connect(server, timeout=connect_timeout)
    except Exception as e:  # noqa: BLE001 —— 连接失败需兜底返回原因
        return False, "", f"连接失败：{e}"
    try:
        remote_path = _upload_script(client, local_script, script_name)
        quoted = shlex.join(map(str, args))
        shell = "bash" if server.credential.username == "root" else "sudo -n bash"
        command = f"{shell} {shlex.quote(remote_path)} {quoted}".strip()
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        if stdin_data:
            stdin.write(stdin_data + "\n")
        stdin.close()
        return _read_command_result(stdout, stderr, "脚本")
    except Exception as e:  # noqa: BLE001 —— 需兜底返回失败原因
        return False, "", str(e)
    finally:
        if remote_path:
            _cleanup_remote_script(client, remote_path)
        client.close()
