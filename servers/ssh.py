"""SSH 连接与命令执行工具。"""

import io
import os

import paramiko


def _load_key(private_key: str, password: str | None = None):
    """按 RSA / Ed25519 / ECDSA 顺序尝试解析私钥，密码作为密钥口令。"""
    errors = []
    for key_cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return key_cls.from_private_key(io.StringIO(private_key), password=password)
        except paramiko.SSHException as e:
            errors.append(f"{key_cls.__name__}: {e}")
    raise paramiko.SSHException("无法解析私钥：" + "；".join(errors))


def test_connection(host, port, username, password=None, private_key=None, timeout=8):
    """尝试连接目标服务器，返回 (是否成功, 信息)。"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs = {"timeout": timeout}
        if private_key:
            key = _load_key(private_key, password)
            client.connect(host, port=port, username=username, pkey=key, **kwargs)
        else:
            client.connect(host, port=port, username=username, password=password, **kwargs)
        return True, f"SSH 连接成功（{username}@{host}:{port}）"
    except Exception as e:  # noqa: BLE001 —— 测试连接需要兜底展示所有失败原因
        return False, f"连接失败：{e}"
    finally:
        client.close()


def test_server_connection(server):
    """对 Server 实例执行连接测试。"""
    cred = server.credential
    return test_connection(
        server.host,
        server.port,
        cred.username,
        password=cred.password,
        private_key=cred.private_key,
    )


def _connect(server, timeout=8):
    """建立 SSH 连接，返回 client（调用方负责 close）。"""
    cred = server.credential
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {"timeout": timeout}
    if cred.private_key:
        key = _load_key(cred.private_key, cred.password)
        client.connect(server.host, port=server.port, username=cred.username, pkey=key, **kwargs)
    else:
        client.connect(server.host, port=server.port, username=cred.username, password=cred.password, **kwargs)
    return client


def exec_command(server, command, timeout=10):
    """在目标服务器执行命令，返回 (ok, stdout, stderr)。连接失败或命令非零退出均返回 False。"""
    try:
        client = _connect(server)
    except Exception as e:  # noqa: BLE001 —— 连接失败需兜底返回原因
        return False, "", f"连接失败：{e}"
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            return False, out, err or f"命令退出码 {exit_status}"
        return True, out, err
    except Exception as e:  # noqa: BLE001 —— 需兜底返回失败原因
        return False, "", str(e)
    finally:
        client.close()


SCRIPT_REMOTE_DIR = "/tmp/nrm_scripts"


def _upload_script(client, local_script: str, remote_name: str) -> str:
    """经 SFTP 将本地脚本上传到目标机 /tmp/nrm_scripts/，返回远端路径。

    自动创建远端目录；脚本按文件名上传（同一文件重复上传自动覆盖）。
    """
    sftp = client.open_sftp()
    try:
        try:
            sftp.stat(SCRIPT_REMOTE_DIR)
        except FileNotFoundError:
            sftp.mkdir(SCRIPT_REMOTE_DIR)
        remote_path = f"{SCRIPT_REMOTE_DIR}/{remote_name}"
        sftp.put(local_script, remote_path)
        # 确保可执行（脚本内部自己处理权限，这里仅兜底）
        sftp.chmod(remote_path, 0o755)
        return remote_path
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
    """将本地脚本上传到目标机并以 sudo 执行，返回 (ok, stdout, stderr)。

    - local_script：代码库内的脚本文件路径（如 servers/scripts/nrm_mgmt.sh）
    - args：传给脚本的子命令参数
    - stdin_data：可选，写入脚本 stdin（如 provision 的 user:password）
    - connect_timeout：SSH 连接超时（秒）。页面渲染路径上的查询（如设备信息）
      应传短超时，避免目标机不可达时拖死页面
    - 统一以 `sudo -n bash <远端脚本> <参数...>` 执行（SSH 用户为 root 时 sudo 直接放行）
    """
    args = args or []
    script_name = os.path.basename(local_script)
    try:
        client = _connect(server, timeout=connect_timeout)
    except Exception as e:  # noqa: BLE001 —— 连接失败需兜底返回原因
        return False, "", f"连接失败：{e}"
    try:
        remote_path = _upload_script(client, local_script, script_name)
        quoted = " ".join(shlex_quote(a) for a in args)
        command = f"sudo -n bash {remote_path} {quoted}".strip()
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        if stdin_data:
            stdin.write(stdin_data + "\n")
        stdin.close()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            return False, out, err or f"脚本退出码 {exit_status}"
        return True, out, err
    except Exception as e:  # noqa: BLE001 —— 需兜底返回失败原因
        return False, "", str(e)
    finally:
        client.close()


def shlex_quote(s: str) -> str:
    """单引号包裹参数，防止注入（远端经 bash 执行）。"""
    return "'" + str(s).replace("'", "'\\''") + "'"
