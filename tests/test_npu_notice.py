"""NPU 分组与公告功能测试（隔离库 + mock SSH，不连真实机器）。"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import Announcement
from credentials.models import Credential
from servers.models import Server

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def server():
    cred = Credential.objects.create(name="c1", username="root", password="p")
    return Server.objects.create(name="npu机", host="10.0.0.1", port=22, credential=cred, is_npu=True)


def test_detect_npu_groups(server):
    from servers.management import detect_npu_groups

    with patch("servers.management._exec", return_value=(True, "/dev/davinci0\n/dev/davinci1\n/dev/davinci2", "")):
        ok, groups, msg = detect_npu_groups(server)
    assert ok and groups == ["npu", "npu0", "npu1", "npu2"]


def test_grant_npu_access(server):
    from servers.management import grant_npu_access

    with patch("servers.management._exec", return_value=(True, "", "")) as m:
        ok, msg = grant_npu_access(server, "alice", ["npu", "npu1"])
    assert ok
    assert "usermod -aG npu,npu1 alice" in m.call_args.args[1]


def test_groups_api_returns_npu(server, client):
    su = User.objects.create_user(username="su", password="x12345!", is_staff=True, is_superuser=True)
    server.npu_groups = "npu,npu0,npu1"
    server.save()
    client.force_login(su)
    r = client.get(reverse("servers:groups_api", args=[server.pk]))
    assert r.json()["extra_groups"] == ["npu", "npu0", "npu1"]
    assert r.json()["is_npu"] is True


def test_write_motd(server):
    from servers.management import write_server_motd

    Announcement.objects.create(title="使用规范", content="禁止挖矿", enabled=True)
    with patch("servers.management._exec", return_value=(True, "", "")) as m:
        ok2, _ = write_server_motd(server)
    cmds = [c.args[1] for c in m.call_args_list]
    assert ok2
    assert any("/etc/motd.d" in c for c in cmds)
    assert not any("nrm_notifications.md" in c for c in cmds)


def test_homepage_shows_announcement(client, server):
    u = User.objects.create_user(username="u1", password="x12345!")
    Announcement.objects.create(title="系统公告标题", content="欢迎使用 NRM", enabled=True)
    client.force_login(u)
    html = client.get(reverse("applications:my")).content.decode()
    assert "系统公告标题" in html
