from django import forms

from credentials.models import Credential

from .models import Server


class ServerForm(forms.ModelForm):
    # 高级设置字段：模板中放入折叠面板，默认收起
    advanced_fields = (
        "nproc_limit",
        "nofile_limit",
        "as_limit",
        "core_limit",
        "fsize_limit",
        "maxlogins_limit",
    )

    credential = forms.ModelChoiceField(
        queryset=Credential.objects.all(),
        required=True,
        label="管理凭据",
        empty_label="请选择已有的管理凭据",
        help_text="凭据统一在“凭据管理”中维护，这里直接选择。",
    )

    class Meta:
        model = Server
        fields = [
            "name",
            "host",
            "port",
            "credential",
            "default_group",
            "is_npu",
            "npu_groups",
            "init_script",
            "nproc_limit",
            "nofile_limit",
            "as_limit",
            "core_limit",
            "fsize_limit",
            "maxlogins_limit",
        ]
        widgets = {
            "port": forms.NumberInput(attrs={"min": 1, "max": 65535}),
            "nproc_limit": forms.NumberInput(attrs={"min": 0}),
            "nofile_limit": forms.NumberInput(attrs={"min": 0}),
            "as_limit": forms.NumberInput(attrs={"min": 0}),
            "core_limit": forms.NumberInput(attrs={"min": 0}),
            "fsize_limit": forms.NumberInput(attrs={"min": 0}),
            "maxlogins_limit": forms.NumberInput(attrs={"min": 0}),
        }
        help_texts = {
            "default_group": "多个分组用英文逗号分隔，如：dev,ops",
            "npu_groups": "NPU 卡组列表，用英文逗号分隔（如 npu,npu0,npu1）；也可保存后在详情页自动检测",
            "nproc_limit": "每个用户最大进程数，0 表示不限制",
            "nofile_limit": "每个用户最大打开文件数，0 表示不限制",
            "as_limit": "每个用户最大虚拟内存（KB），0 表示不限制",
            "core_limit": "核心转储文件大小（KB），建议 0 防占磁盘，0 表示不限制",
            "fsize_limit": "每个用户最大单文件大小（KB），0 表示不限制",
            "maxlogins_limit": "每个用户最大同时登录会话数，0 表示不限制",
        }
