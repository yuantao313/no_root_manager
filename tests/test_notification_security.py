"""通知权限、SSRF 与秘密不回显回归测试。"""

from unittest.mock import MagicMock, patch
from urllib.request import Request

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from applications.models import Application
from credentials.models import Credential
from notifications.models import EmailConfig, WebhookConfig
from notifications.security import (
    MAX_WEBHOOK_RESPONSE_BYTES,
    UnsafeWebhookURL,
    _BoundedWebhookResponse,
    _PinnedHTTPSConnection,
    open_webhook_request,
    validate_webhook_url,
)
from notifications.services import admin_emails, send_webhook_to, webhook_new_application
from servers.models import Server, ServerAdminBinding

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "https://127.0.0.1/hook",
        "https://10.0.0.2/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/hook",
        "https://user:password@example.com/hook",
    ],
)
def test_webhook_url_rejects_unsafe_targets(url):
    with pytest.raises(UnsafeWebhookURL):
        validate_webhook_url(url)


def test_webhook_dns_resolution_rejects_private_result():
    request = Request("https://hooks.example.com/event", data=b"{}", method="POST")
    resolved = [(2, 1, 6, "", ("192.168.1.8", 443))]
    with patch("notifications.security.socket.getaddrinfo", return_value=resolved):
        with pytest.raises(UnsafeWebhookURL):
            open_webhook_request(request, timeout=5)


def test_webhook_public_target_pins_verified_ip_and_preserves_host():
    request = Request("https://hooks.example.com/event", data=b"{}", method="POST")
    resolved = [(2, 1, 6, "", ("93.184.216.34", 443))]
    response = type("Response", (), {"status": 200})()
    with (
        patch("notifications.security.socket.getaddrinfo", return_value=resolved),
        patch("notifications.security._PinnedHTTPSConnection") as connection,
    ):
        connection.return_value.getresponse.return_value = response
        wrapped = open_webhook_request(request, timeout=7)
    connection_args = connection.call_args.args
    assert connection_args[:3] == ("hooks.example.com", 443, "93.184.216.34")
    assert 0 < connection_args[3] <= 7
    args = connection.return_value.request.call_args
    assert args.args[:2] == ("POST", "/event")
    assert args.kwargs["headers"]["Host"] == "hooks.example.com"
    assert wrapped.status == 200


def test_pinned_connection_never_resolves_hostname_again():
    connection = _PinnedHTTPSConnection("hooks.example.com", 443, "93.184.216.34", 7)
    sentinel = object()
    with patch("notifications.security.socket.create_connection", return_value=sentinel) as connect:
        assert connection._create_connection(("hooks.example.com", 443), 7, None) is sentinel
    connect.assert_called_once_with(("93.184.216.34", 443), 7, None)


def test_webhook_response_body_is_bounded():
    connection = type("Connection", (), {"sock": None, "close": lambda self: None})()
    deadline_socket = type("Socket", (), {"settimeout": lambda self, timeout: None})()

    class Response:
        status = 200

        def getheader(self, name):  # noqa: ARG002
            return None

        def read1(self, amount):
            return b"x" * amount

        def close(self):
            pass

    wrapped = _BoundedWebhookResponse(connection, Response(), float("inf"), deadline_socket)
    with pytest.raises(UnsafeWebhookURL, match="64 KiB"):
        wrapped.read()
    assert MAX_WEBHOOK_RESPONSE_BYTES == 64 * 1024


def test_webhook_deadline_uses_retained_socket_after_connection_close():
    connection = type("Connection", (), {"sock": None, "close": lambda self: None})()
    deadline_socket = MagicMock()

    class Response:
        status = 200

        def getheader(self, name):  # noqa: ARG002
            return None

        def read1(self, amount):  # noqa: ARG002
            return b"x"

        def close(self):
            pass

    wrapped = _BoundedWebhookResponse(connection, Response(), 10.0, deadline_socket)
    with (
        patch("notifications.security.time.monotonic", side_effect=[9.0, 10.1]),
        pytest.raises(TimeoutError, match="读取超时"),
    ):
        wrapped.read()
    deadline_socket.settimeout.assert_called_once_with(1.0)


def test_send_webhook_rejects_http_before_network():
    with patch("notifications.services.open_webhook_request") as mock_open:
        ok, message = send_webhook_to("http://example.com/hook", "")
    assert ok is False
    assert "HTTPS" in message
    mock_open.assert_not_called()


@pytest.fixture
def scoped_application():
    server = Server.objects.create(name="scope-server", host="192.0.2.10")
    return Application.objects.create(
        applicant_name="申请人",
        username="applicant",
        email="applicant@example.com",
        target_server=server,
        description="权限范围测试",
    )


def test_application_notifications_follow_server_scope(scoped_application):
    superuser = User.objects.create_user(
        username="super",
        password="x",
        email="super@example.com",
        is_staff=True,
        is_superuser=True,
    )
    bound = User.objects.create_user(
        username="bound",
        password="x",
        email="bound@example.com",
        is_staff=True,
    )
    unbound = User.objects.create_user(
        username="unbound",
        password="x",
        email="unbound@example.com",
        is_staff=True,
    )
    inactive = User.objects.create_user(
        username="inactive",
        password="x",
        email="inactive@example.com",
        is_staff=True,
        is_active=False,
    )
    demoted = User.objects.create_user(
        username="demoted",
        password="x",
        email="demoted@example.com",
        is_staff=False,
    )
    ServerAdminBinding.objects.create(server=scoped_application.target_server, admin=bound)
    ServerAdminBinding.objects.create(server=scoped_application.target_server, admin=inactive)
    ServerAdminBinding.objects.create(server=scoped_application.target_server, admin=demoted)

    assert set(admin_emails(scoped_application)) == {"super@example.com", "bound@example.com"}

    hooks = {
        "global": WebhookConfig.objects.create(name="generic", url="https://hooks.example.com/global", owner=None),
        "super": WebhookConfig.objects.create(name="generic", url="https://hooks.example.com/super", owner=superuser),
        "bound": WebhookConfig.objects.create(name="generic", url="https://hooks.example.com/bound", owner=bound),
        "unbound": WebhookConfig.objects.create(name="generic", url="https://hooks.example.com/unbound", owner=unbound),
        "inactive": WebhookConfig.objects.create(
            name="generic", url="https://hooks.example.com/inactive", owner=inactive
        ),
        "demoted": WebhookConfig.objects.create(name="generic", url="https://hooks.example.com/demoted", owner=demoted),
    }
    with patch("notifications.services._post_webhook", return_value=(True, "ok")) as mock_post:
        assert webhook_new_application(scoped_application) is True
    pushed_urls = {call.args[0] for call in mock_post.call_args_list}
    assert pushed_urls == {hooks["global"].url, hooks["super"].url, hooks["bound"].url}
    assert hooks["unbound"].url not in pushed_urls
    assert hooks["inactive"].url not in pushed_urls
    assert hooks["demoted"].url not in pushed_urls


def test_settings_and_admin_do_not_render_stored_secrets(client):
    admin = User.objects.create_superuser("secret-admin", "admin@example.com", "x12345!")
    client.force_login(admin)
    email = EmailConfig.objects.create(
        host="smtp.example.com",
        username="mailer",
        password="SMTP-PASSWORD-MUST-NOT-RENDER",
        mail_webhook_url="https://hooks.example.com/MAIL-URL-MUST-NOT-RENDER",
        mail_webhook_token="MAIL-TOKEN-MUST-NOT-RENDER",
    )
    hook = WebhookConfig.objects.create(
        name="generic",
        url="https://hooks.example.com/GLOBAL-URL-MUST-NOT-RENDER",
        secret="GLOBAL-SECRET-MUST-NOT-RENDER",
    )
    WebhookConfig.objects.create(
        name="generic",
        url="https://hooks.example.com/PERSONAL-URL-MUST-NOT-RENDER",
        secret="PERSONAL-SECRET-MUST-NOT-RENDER",
        owner=admin,
    )
    credential = Credential.objects.create(
        name="credential",
        username="root",
        password="CREDENTIAL-PASSWORD-MUST-NOT-RENDER",
        private_key="PRIVATE-KEY-MUST-NOT-RENDER",
    )
    application = Application.objects.create(
        applicant=admin,
        username="secret-admin",
        initial_password="APPLICATION-PASSWORD-MUST-NOT-RENDER",
    )

    pages = [
        reverse("accounts:settings"),
        reverse("accounts:profile"),
        reverse("admin:notifications_emailconfig_change", args=[email.pk]),
        reverse("admin:notifications_webhookconfig_change", args=[hook.pk]),
        reverse("admin:credentials_credential_change", args=[credential.pk]),
        reverse("admin:applications_application_change", args=[application.pk]),
    ]
    html = "\n".join(client.get(url).content.decode() for url in pages)
    for secret in (
        "SMTP-PASSWORD-MUST-NOT-RENDER",
        "MAIL-URL-MUST-NOT-RENDER",
        "MAIL-TOKEN-MUST-NOT-RENDER",
        "GLOBAL-URL-MUST-NOT-RENDER",
        "GLOBAL-SECRET-MUST-NOT-RENDER",
        "PERSONAL-URL-MUST-NOT-RENDER",
        "PERSONAL-SECRET-MUST-NOT-RENDER",
        "CREDENTIAL-PASSWORD-MUST-NOT-RENDER",
        "PRIVATE-KEY-MUST-NOT-RENDER",
        "APPLICATION-PASSWORD-MUST-NOT-RENDER",
    ):
        assert secret not in html


def test_invalid_credential_form_does_not_echo_submitted_secrets(client):
    admin = User.objects.create_superuser("credential-admin", "admin@example.com", "x12345!")
    client.force_login(admin)
    response = client.post(
        reverse("admin:credentials_credential_add"),
        {
            "name": "",
            "username": "root",
            "password": "FORM-PASSWORD-MUST-NOT-RENDER",
            "private_key": "FORM-PRIVATE-KEY-MUST-NOT-RENDER",
            "remark": "",
        },
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert "FORM-PASSWORD-MUST-NOT-RENDER" not in html
    assert "FORM-PRIVATE-KEY-MUST-NOT-RENDER" not in html
