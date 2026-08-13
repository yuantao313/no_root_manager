"""NPU 分组与公告功能测试（隔离库 + mock SSH，不连真实机器）。

公告相关用例：markdown 子集 → HTML（首页）/ ANSI（motd）转换，
转换器本身的细粒度用例见 test_markdown_convert.py。
"""

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

    with patch("servers.management._run_mgmt", return_value=(True, "OK grant_npu alice", "")) as m:
        ok, msg = grant_npu_access(server, "alice", ["npu", "npu1"])
    assert ok
    # 收敛到脚本子命令 grant_npu <user> <groups_csv>
    args = m.call_args.args[1]
    assert args[0] == "grant_npu"
    assert args[1] == "alice"
    assert args[2] == "npu,npu1"


def test_groups_api_returns_npu(server, client):
    """groups API：NPU 卡组走内存缓存（首次未命中时由 detect_npu_groups 检测）。"""
    from servers.management import clear_npu_state_cache

    clear_npu_state_cache()
    su = User.objects.create_user(username="su", password="x12345!", is_staff=True, is_superuser=True)
    server.npu_groups = "npu,npu0,npu1"
    server.save()
    client.force_login(su)
    # mock SSH 检测（避免连真实机器），返回库内卡组
    with patch("servers.management.detect_npu_groups", return_value=(True, ["npu", "npu0", "npu1"], "OK")):
        r = client.get(reverse("servers:groups_api", args=[server.pk]))
    assert r.json()["extra_groups"] == ["npu", "npu0", "npu1"]
    assert r.json()["is_npu"] is True


def test_write_motd(server):
    from servers.management import write_server_motd

    Announcement.objects.create(content="禁止挖矿", enabled=True)
    with patch("servers.management._exec", return_value=(True, "", "")) as m:
        ok2, _ = write_server_motd(server)
    cmds = [c.args[1] for c in m.call_args_list]
    assert ok2
    assert any("/etc/motd.d" in c for c in cmds)


def test_write_motd_uses_markdown_ansi(server):
    """公告 markdown 渲染为 ANSI 彩色文本写入 motd（颜色/加粗高亮）。"""
    from servers.management import write_server_motd

    Announcement.objects.create(content="本周维护：{red}禁止挖矿{/red}，**全员注意**", enabled=True)
    with patch("servers.management._exec", return_value=(True, "", "")) as m:
        ok2, _ = write_server_motd(server)
    cmds = [c.args[1] for c in m.call_args_list]
    assert ok2
    motd_cmd = [c for c in cmds if "/etc/motd.d" in c][0]
    assert "\x1b[31m禁止挖矿\x1b[0m" in motd_cmd  # 颜色转义随 motd 写入
    assert "\x1b[1m全员注意\x1b[0m" in motd_cmd  # 加粗


def test_write_motd_no_announcement_clears(server):
    """无启用公告：清除 motd 文件，不留残留。"""
    from servers.management import write_server_motd

    with patch("servers.management._exec", return_value=(True, "", "")) as m:
        ok2, msg = write_server_motd(server)
    cmds = [c.args[1] for c in m.call_args_list]
    assert ok2
    assert any(c.startswith("rm -f") and "/etc/motd.d" in c for c in cmds)
    assert "清除" in msg


def test_announcement_save_markdown(client):
    """公告保存：markdown 源码入库，设置页编辑器回显。"""
    su = User.objects.create_user(username="su", password="x12345!", is_staff=True, is_superuser=True)
    client.force_login(su)
    resp = client.post(
        reverse("accounts:settings"),
        {
            "add_announcement": "1",
            "content": "维护：**今晚** {red}开始{/red}",
            "enabled": "on",
        },
    )
    assert resp.status_code == 302
    ann = Announcement.objects.first()
    assert ann.content == "维护：**今晚** {red}开始{/red}"
    assert "开始" in ann.content_html
    assert "<strong>今晚</strong>" in ann.content_html
    # 设置页回显已有 markdown 源码
    html = client.get(reverse("accounts:settings")).content.decode()
    assert "维护：**今晚**" in html


def test_homepage_shows_announcement_html(client, server):
    """首页公告栏：markdown 渲染为 HTML 展示（含样式标签）。"""
    u = User.objects.create_user(username="u1", password="x12345!")
    Announcement.objects.create(content="欢迎使用 **NRM**", enabled=True)
    client.force_login(u)
    html = client.get(reverse("applications:my")).content.decode()
    assert "欢迎使用" in html
    assert "<strong>NRM</strong>" in html


def test_homepage_skips_disabled_announcement(client, server):
    u = User.objects.create_user(username="u2", password="x12345!")
    Announcement.objects.create(content="不展示", enabled=False)
    client.force_login(u)
    html = client.get(reverse("applications:my")).content.decode()
    assert "不展示" not in html
