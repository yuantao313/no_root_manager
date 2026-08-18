import re

from django import forms

from credentials.models import Credential

from .models import ROOT_EQUIVALENT_GROUPS, Server
from .ssh import normalize_host_key_fingerprint

SERVER_EDIT_FIELDS = (
    "name",
    "host",
    "port",
    "ssh_host_key_fingerprint",
    "credential",
    "default_group",
)


class ServerForm(forms.ModelForm):
    credential = forms.ModelChoiceField(
        queryset=Credential.objects.all(),
        required=True,
        label="管理凭据",
        empty_label="请选择已有的管理凭据",
        help_text="凭据统一在“凭据管理”中维护，这里直接选择。",
    )

    class Meta:
        model = Server
        fields = SERVER_EDIT_FIELDS
        widgets = {
            "port": forms.NumberInput(attrs={"min": 1, "max": 65535}),
        }
        labels = {
            "default_group": "新账号默认用户组",
        }
        help_texts = {
            "default_group": (
                "仅在“申请服务器账号”审批开通时自动加入；多个分组用英文逗号分隔，如：dev,ops。"
                "转移已有账号不自动加组；sudo、wheel、docker 等高危权限必须单独申请。"
            ),
        }

    def clean_default_group(self):
        """默认组会自动授予每个新账号，校验并规范化配置。"""
        value = self.cleaned_data.get("default_group", "")
        groups = [group.strip() for group in value.split(",") if group.strip()]
        dangerous = sorted(set(groups) & ROOT_EQUIVALENT_GROUPS)
        if dangerous:
            raise forms.ValidationError(
                f"默认用户组不能包含 root 级权限组：{', '.join(dangerous)}；请改走独立权限申请。"
            )
        invalid = [group for group in groups if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,31}", group)]
        if invalid:
            raise forms.ValidationError(
                f"用户组名称不合法：{', '.join(invalid)}；请使用英文字母、数字、下划线或连字符。"
            )
        # 保持管理员填写顺序，同时避免脚本重复处理同一分组。
        return ",".join(dict.fromkeys(groups))

    def clean_ssh_host_key_fingerprint(self):
        """仅接受 OpenSSH SHA256 指纹。

        首次“保存并测试连接”允许留空，由 SSH 层只读获取候选指纹；
        普通保存必须已经填入经管理员核对的指纹。
        """
        value = self.cleaned_data.get("ssh_host_key_fingerprint", "")
        if not value:
            if self.data.get("action") == "test":
                return ""
            raise forms.ValidationError("请先获取并核对 SSH 主机指纹，再保存服务器。")
        try:
            return normalize_host_key_fingerprint(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
