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

    Announcement.objects.create(title="使用规范", content="禁止挖矿", enabled=True)
    with patch("servers.management._exec", return_value=(True, "", "")) as m:
        ok2, _ = write_server_motd(server)
    cmds = [c.args[1] for c in m.call_args_list]
    assert ok2
    assert any("/etc/motd.d" in c for c in cmds)
    assert not any("nrm_notifications.md" in c for c in cmds)


def test_write_motd_uses_html_ansi(server):
    """公告 HTML 内容渲染为 ANSI 彩色文本写入 motd（支持高亮调色）。"""
    from servers.management import write_server_motd

    Announcement.objects.create(
        title="规范",
        content="禁止挖矿",
        html_content='<p>本周维护：<span style="color:red">禁止挖矿</span></p>',
        enabled=True,
    )
    with patch("servers.management._exec", return_value=(True, "", "")) as m:
        ok2, _ = write_server_motd(server)
    cmds = [c.args[1] for c in m.call_args_list]
    assert ok2
    motd_cmd = [c for c in cmds if "/etc/motd.d" in c][0]
    assert "\x1b[" in motd_cmd  # ANSI 转义码随 motd 写入
    assert "禁止挖矿" in motd_cmd


def test_html_to_ansi_colors():
    """HTML→ANSI：颜色/背景/加粗正确映射，普通文本保留。"""
    from servers.management import _html_to_ansi

    html = (
        '<p>高亮：<span style="background-color:yellow">注意</span> '
        '与 <span style="color:red">红色</span> 与 <b>加粗</b></p>'
    )
    out = _html_to_ansi(html)
    assert ";43m" in out  # 黄色背景
    assert "31m" in out  # 红色前景
    assert "1m" in out  # 加粗
    assert "注意" in out and "红色" in out and "加粗" in out


def test_html_to_ansi_plain_text():
    """无样式 HTML 降级为纯文本（不含转义码）。"""
    from servers.management import _html_to_ansi

    out = _html_to_ansi("<p>纯文本公告</p><p>第二行</p>")
    assert "纯文本公告" in out and "第二行" in out
    assert "\x1b[" not in out


def test_html_to_ansi_lists():
    """列表：ul 用 • 项目符号，ol 用递增序号。"""
    from servers.management import _html_to_ansi

    out = _html_to_ansi("<ul><li>项目A</li><li>项目B</li></ul><ol><li>第一步</li><li>第二步</li></ol>")
    assert "• 项目A" in out and "• 项目B" in out
    assert "1. 第一步" in out and "2. 第二步" in out


def test_html_to_ansi_link():
    """链接：输出 文字（URL），终端可见地址。"""
    from servers.management import _html_to_ansi

    out = _html_to_ansi('<p>详情见 <a href="https://example.com/doc">帮助文档</a></p>')
    assert "帮助文档（https://example.com/doc）" in out


def test_html_to_ansi_heading_shades():
    """标题：h1~h4 用不同深浅颜色区分（亮白/亮黄/黄/亮灰）。"""
    from servers.management import _html_to_ansi

    out = _html_to_ansi("<h1>一级</h1><h2>二级</h2><h3>三级</h3><h4>四级</h4>")
    assert "\x1b[1;97m一级\x1b[0m" in out  # h1 亮白（最深）
    assert "\x1b[1;93m二级\x1b[0m" in out  # h2 亮黄
    assert "\x1b[1;33m三级\x1b[0m" in out  # h3 黄
    assert "\x1b[1;37m四级\x1b[0m" in out  # h4 亮灰（最浅）


def test_announcement_save_keeps_html(client):
    """公告保存：html_content 入库，设置页编辑器回显。"""
    su = User.objects.create_user(username="su", password="x12345!", is_staff=True, is_superuser=True)
    client.force_login(su)
    resp = client.post(
        reverse("accounts:settings"),
        {
            "add_announcement": "1",
            "title": "维护公告",
            "html_content": '<p>维护 <span style="color:red">今晚</span> 开始</p>',
            "content": "维护 今晚 开始",
            "enabled": "on",
        },
    )
    assert resp.status_code == 302
    ann = Announcement.objects.first()
    assert ann.title == "维护公告"
    assert "color:red" in ann.html_content
    assert ann.content == "维护 今晚 开始"
    # 设置页回显已有 HTML
    html = client.get(reverse("accounts:settings")).content.decode()
    assert "color:red" in html


def test_homepage_shows_announcement(client, server):
    u = User.objects.create_user(username="u1", password="x12345!")
    Announcement.objects.create(title="系统公告标题", content="欢迎使用 NRM", enabled=True)
    client.force_login(u)
    html = client.get(reverse("applications:my")).content.decode()
    assert "系统公告标题" in html
