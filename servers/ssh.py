"""SSH 连接与命令执行工具。"""

import io

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
