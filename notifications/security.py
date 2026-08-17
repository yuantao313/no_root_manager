"""Webhook 出站请求安全边界。"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import time
import urllib.request
from urllib.parse import urlsplit

MAX_WEBHOOK_RESPONSE_BYTES = 64 * 1024


class UnsafeWebhookURL(ValueError):
    """Webhook URL 不满足平台出站安全策略。"""


def _require_public_ip(value: str) -> None:
    """拒绝任何非公网地址，包括回环、内网、链路本地和保留地址。"""
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as exc:
        raise UnsafeWebhookURL("Webhook 主机解析结果不是有效 IP 地址。") from exc
    if not address.is_global:
        raise UnsafeWebhookURL("Webhook 不允许访问本机、内网、链路本地或保留地址。")


def _resolve_public_ips(hostname: str, port: int) -> list[str]:
    """解析并验证全部地址，返回本次请求允许使用的固定公网 IP 列表。"""
    try:
        addresses = list(
            dict.fromkeys(
                sockaddr[0]
                for _family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(
                    hostname,
                    port,
                    type=socket.SOCK_STREAM,
                )
            )
        )
    except OSError as exc:
        raise UnsafeWebhookURL("Webhook 主机名无法解析。") from exc
    if not addresses:
        raise UnsafeWebhookURL("Webhook 主机名没有可用地址。")
    for address in addresses:
        _require_public_ip(address)
    return addresses


def validate_webhook_url(url: str) -> str:
    """校验 Webhook URL 的静态结构；发送时再解析并固定公网 IP。"""
    normalized = (url or "").strip()
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeWebhookURL("Webhook URL 端口无效。") from exc

    if parsed.scheme.lower() != "https":
        raise UnsafeWebhookURL("Webhook URL 必须使用 HTTPS。")
    if port is not None and not 1 <= port <= 65535:
        raise UnsafeWebhookURL("Webhook URL 端口无效。")
    if not parsed.hostname:
        raise UnsafeWebhookURL("Webhook URL 缺少有效主机名。")
    if parsed.username or parsed.password:
        raise UnsafeWebhookURL("Webhook URL 不允许包含用户名或密码。")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise UnsafeWebhookURL("Webhook 不允许访问本地主机名。")

    try:
        direct_ip = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        direct_ip = None
    if direct_ip is not None:
        _require_public_ip(str(direct_ip))

    return normalized


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """连接已验证 IP，同时以原始主机名完成 Host、SNI 和证书校验。"""

    def __init__(self, hostname: str, port: int, verified_ip: str, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout)
        self.verified_ip = verified_ip
        # HTTPConnection.connect 会调用该实例属性；固定为 IP 字面量后不再二次 DNS 解析。
        self._create_connection = self._connect_verified_ip

    def _connect_verified_ip(self, address, timeout, source_address):  # noqa: ARG002
        return socket.create_connection((self.verified_ip, self.port), timeout, source_address)


class _BoundedWebhookResponse:
    """限制响应体大小和读取总时长，并确保底层连接关闭。"""

    def __init__(self, connection, response, deadline: float, deadline_socket):
        self._connection = connection
        self._response = response
        self._deadline = deadline
        self._deadline_socket = deadline_socket
        self.status = response.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        try:
            self._response.close()
        finally:
            self._connection.close()

    def read(self) -> bytes:
        content_length = self._response.getheader("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_WEBHOOK_RESPONSE_BYTES:
                    raise UnsafeWebhookURL("Webhook 响应体超过 64 KiB 限制。")
            except ValueError:
                pass

        chunks = []
        total = 0
        while True:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Webhook 响应读取超时。")
            # Connection: close 会令 HTTPConnection.sock 变为 None；保留发起请求时的
            # TLS socket 引用，才能在每次读取前按总 deadline 收紧超时。
            self._deadline_socket.settimeout(remaining)
            chunk = self._response.read1(min(8192, MAX_WEBHOOK_RESPONSE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_WEBHOOK_RESPONSE_BYTES:
                raise UnsafeWebhookURL("Webhook 响应体超过 64 KiB 限制。")
        return b"".join(chunks)


def open_webhook_request(request: urllib.request.Request, *, timeout: int):
    """验证目标并把连接固定到同一次 DNS 校验得到的公网 IP。"""
    deadline = time.monotonic() + timeout
    normalized = validate_webhook_url(request.full_url)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname.rstrip(".").lower()
    port = parsed.port or 443
    try:
        direct_ip = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        verified_ips = _resolve_public_ips(hostname, port)
    else:
        _require_public_ip(str(direct_ip))
        verified_ips = [str(direct_ip)]

    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    headers = dict(request.header_items())
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    if port != 443:
        host_header = f"{host_header}:{port}"
    headers["Host"] = host_header
    headers["Connection"] = "close"
    last_error = None
    for verified_ip in verified_ips:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Webhook 连接超时。") from last_error
        connection = _PinnedHTTPSConnection(hostname, port, verified_ip, remaining)
        try:
            connection.request(request.get_method(), target, body=request.data, headers=headers)
            deadline_socket = connection.sock
            if deadline_socket is None:
                raise OSError("Webhook TLS 连接未建立。")
            response = connection.getresponse()
        except Exception as exc:  # noqa: BLE001 —— 逐个尝试同次校验得到的公网地址
            last_error = exc
            connection.close()
            continue
        return _BoundedWebhookResponse(connection, response, deadline, deadline_socket)
    raise OSError("Webhook 的已验证公网地址均无法连接。") from last_error
