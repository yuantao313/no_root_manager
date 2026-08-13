"""markdown 子集转换器测试：HTML 与终端 ANSI 双输出。"""

import pytest

from accounts.markdown_convert import markdown_to_ansi, markdown_to_html


@pytest.mark.parametrize(
    ("source", "expect"),
    [
        ("# 标题", "<h1>标题</h1>"),
        ("## 标题", "<h2>标题</h2>"),
        ("### 标题", "<h3>标题</h3>"),
        ("正文**加粗**尾部", "<p>正文<strong>加粗</strong>尾部</p>"),
        ("*斜体*", "<p><em>斜体</em></p>"),
        ("{red}红字{/red}", '<p><span style="color:red">红字</span></p>'),
        ("[谷歌](google.com)", '<p><a href="google.com" target="_blank" rel="noopener noreferrer">谷歌</a></p>'),
        ("多行\n正文", "<p>多行<br>正文</p>"),
    ],
)
def test_markdown_to_html_subset(source, expect):
    assert markdown_to_html(source) == expect


def test_html_escapes_unsafe_input():
    """普通文本与危险内容被转义，不产生额外标签。"""
    out = markdown_to_html("<script>alert(1)</script> & <b>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<b>" not in out


def test_html_blocks_split_on_blank_line():
    out = markdown_to_html("第一段\n\n第二段")
    assert out.count("<p>") == 2


def test_link_unsafe_scheme_renders_text_only():
    """危险协议（javascript:）不生成超链接，仅渲染文字。"""
    out = markdown_to_html("[点我](javascript:alert(1))")
    assert 'href="javascript:' not in out
    assert "点我" in out


def test_ansi_headings_use_yellow_shades():
    """终端标题映射：h1 亮黄(93)、h2 暗黄(33)、h3 灰(90)。"""
    assert markdown_to_ansi("# 一级") == "\x1b[93m一级\x1b[0m"
    assert markdown_to_ansi("## 二级") == "\x1b[33m二级\x1b[0m"
    assert markdown_to_ansi("### 三级") == "\x1b[90m三级\x1b[0m"


def test_ansi_inline_styles():
    out = markdown_to_ansi("**加粗**与*斜体*")
    assert "\x1b[1m加粗\x1b[0m" in out
    assert "\x1b[3m斜体\x1b[0m" in out


def test_ansi_color():
    out = markdown_to_ansi("{red}红字{/red}")
    assert "\x1b[31m红字\x1b[0m" in out


def test_ansi_link_uses_fullwidth_parenthesis():
    """链接在终端显示为：文字（URL）。"""
    out = markdown_to_ansi("[谷歌](google.com)")
    assert "谷歌（google.com）" in out


def test_ansi_plain_text_no_escape_codes():
    assert "\x1b[" not in markdown_to_ansi("纯文本公告")
