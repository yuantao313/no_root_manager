"""公告 Markdown 子集转换器：同一份 markdown 文本渲染为 HTML 与终端 ANSI 控制流。

支持的子集（仅此子集，其余原样保留为纯文本）：
  - 标题：`# ` h1 / `## ` h2 / `### ` h3
  - 加粗：`**文字**`
  - 斜体：`*文字*`
  - 颜色：`{red}文字{/red}`（调色板见 ``_COLOR_ANSI``，不支持嵌套颜色）
  - 链接：`[谷歌](google.com)` → HTML 为超链接，终端为 ``谷歌（google.com）``

- ``markdown_to_html``：用于系统首页公告栏渲染
- ``markdown_to_ansi``：用于写入目标机 motd（SSH 登录时终端解析 ANSI 颜色）

终端标题映射：h1 亮黄(93) / h2 暗黄(33) / h3 灰(90)。
HTML 输出仅产生受控标签（h1~h3/p/strong/em/span/a/br），可安全 ``|safe``。
"""

import re

# 颜色名 → 终端 SGR 前景色码（含亮色 90~97）
_COLOR_ANSI = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
    "gray": 90,
    "grey": 90,
    "darkred": 91,
    "darkgreen": 92,
    "darkyellow": 93,
    "darkblue": 94,
    "darkmagenta": 95,
    "darkcyan": 96,
    "orange": 33,
    "purple": 35,
    "pink": 95,
    "brown": 33,
}

# 标题级别 → 终端颜色码
_HEADING_ANSI = {"1": 93, "2": 33, "3": 90}

# 允许的链接协议（其余如 javascript: / data: 一律禁用，只渲染文字）
_ALLOWED_SCHEMES = {"http", "https", "mailto"}
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"\*([^*]+)\*")
_COLOR_RE = re.compile(r"\{([A-Za-z]+)\}(.*?)\{/\1\}")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")


def _escape_html(text: str) -> str:
    """HTML 转义（先 & 后其余，避免双重转义）。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _safe_url(url: str) -> str:
    """校验链接协议：危险协议返回空串（仅渲染文字），否则返回原 URL。"""
    url = url.strip()
    m = _SCHEME_RE.match(url)
    if m and m.group(1).lower() not in _ALLOWED_SCHEMES:
        return ""
    return url


def _ansi(code: int, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m"


def _link_html(label: str, url: str) -> str:
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'


def _link_ansi(label: str, url: str) -> str:
    return f"{label}（{url}）"


def _convert_basic(text: str, html: bool) -> str:
    """行内基础转换：链接 → 加粗 → 斜体（用占位符避免二次匹配）。"""
    tokens: list[str] = []

    def placeholder(rendered: str) -> str:
        key = f"\x00{len(tokens)}\x00"
        tokens.append(rendered)
        return key

    def link_sub(m: re.Match) -> str:
        label = m.group(1)
        url = _safe_url(m.group(2))
        if not url:
            return placeholder(label)
        if html:
            return placeholder(_link_html(_escape_html(label), _escape_html(url)))
        return placeholder(_link_ansi(label, url))

    text = _LINK_RE.sub(link_sub, text)
    text = _BOLD_RE.sub(
        lambda m: placeholder(f"<strong>{m.group(1)}</strong>" if html else _ansi(1, m.group(1))),
        text,
    )
    text = _ITALIC_RE.sub(
        lambda m: placeholder(f"<em>{m.group(1)}</em>" if html else _ansi(3, m.group(1))),
        text,
    )
    for i, rendered in enumerate(tokens):
        text = text.replace(f"\x00{i}\x00", rendered)
    return text


def _convert_inline(text: str, html: bool) -> str:
    """行内完整转换：先转义/链接/颜色（颜色内部可含加粗斜体链接），再基础转换。"""
    if html:
        text = _escape_html(text)

    def color_sub(m: re.Match) -> str:
        color = m.group(1).lower()
        body = _convert_basic(m.group(2), html)
        if html:
            return f'<span style="color:{color}">{body}</span>'
        code = _COLOR_ANSI.get(color)
        return _ansi(code, body) if code is not None else body

    text = _LINK_RE.sub(
        lambda m: (
            _escape_html(m.group(1))
            if html and not _safe_url(m.group(2))
            else (
                f'<a href="{_escape_html(_safe_url(m.group(2)))}" target="_blank" rel="noopener noreferrer">{_escape_html(m.group(1))}</a>'
            )
            if html
            else _convert_basic(f"[{m.group(1)}]({m.group(2)})", html)
        ),
        text,
    )
    text = _COLOR_RE.sub(color_sub, text)
    return _convert_basic(text, html)


def _render_blocks(text: str, html: bool) -> list[tuple[str, str]]:
    """按行解析为块列表 [(标签, 内容)]：h1/h2/h3 与 p 段落。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[tuple[str, str]] = []
    para: list[str] = []

    def flush():
        if para:
            joiner = "<br>" if html else "\n"
            blocks.append(("p", joiner.join(_convert_inline(line, html) for line in para)))
            para.clear()

    for line in lines:
        hm = _HEADING_RE.match(line)
        if hm:
            flush()
            level = len(hm.group(1))
            blocks.append((f"h{level}", _convert_inline(hm.group(2), html)))
        elif line.strip() == "":
            flush()
        else:
            para.append(line)
    flush()
    return blocks


def markdown_to_html(text: str) -> str:
    """Markdown 子集 → 受控 HTML（用于系统首页公告栏，模板可 |safe）。"""
    text = (text or "").strip()
    if not text:
        return ""
    parts = []
    for tag, content in _render_blocks(text, html=True):
        if tag.startswith("h"):
            parts.append(f"<{tag}>{content}</{tag}>")
        else:
            parts.append(f"<p>{content}</p>")
    return "\n".join(parts)


def markdown_to_ansi(text: str) -> str:
    """Markdown 子集 → 终端 ANSI 控制流（用于 motd，SSH 登录时显示颜色）。"""
    text = (text or "").strip()
    if not text:
        return ""
    parts = []
    for tag, content in _render_blocks(text, html=False):
        if tag.startswith("h"):
            parts.append(_ansi(_HEADING_ANSI[tag[1]], content))
        else:
            parts.append(content)
    return "\n\n".join(parts)
