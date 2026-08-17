class WriteOnlyWidgetMixin:
    """敏感输入控件不把已提交或已存值重新渲染进 HTML。"""

    def format_value(self, value):  # noqa: ARG002
        return ""
