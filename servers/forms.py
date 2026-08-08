from django import forms

from credentials.models import Credential

from .models import Server, UserGroup


class ServerForm(forms.ModelForm):
    credential = forms.ModelChoiceField(
        queryset=Credential.objects.all(),
        required=True,
        label="管理凭据",
        empty_label="请选择已有的管理凭据",
        help_text="凭据统一在“凭据管理”中维护，这里直接选择。",
    )
    default_group = forms.ModelChoiceField(
        queryset=UserGroup.objects.none(),
        required=False,
        label="默认申请的用户分组",
        help_text="用户申请账号时默认加入的分组",
    )
    extra_groups = forms.ModelMultipleChoiceField(
        queryset=UserGroup.objects.none(),
        required=False,
        label="可附加申请的用户分组",
        help_text="用户申请时可附加选择加入的分组",
    )

    class Meta:
        model = Server
        fields = ["name", "host", "port", "credential", "default_group", "extra_groups", "nproc_limit", "nofile_limit"]
        widgets = {
            "port": forms.NumberInput(attrs={"min": 1, "max": 65535}),
            "nproc_limit": forms.NumberInput(attrs={"min": 0}),
            "nofile_limit": forms.NumberInput(attrs={"min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 默认组/附加组仅列出当前服务器的分组（新建时无分组可先保存再配置）
        server = kwargs.get("instance")
        qs = UserGroup.objects.filter(server=server) if server else UserGroup.objects.none()
        self.fields["default_group"].queryset = qs
        self.fields["extra_groups"].queryset = qs
        if server:
            self.fields["default_group"].initial = server.default_group
            self.fields["extra_groups"].initial = server.extra_groups.all()
