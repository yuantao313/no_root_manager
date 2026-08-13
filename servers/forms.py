from django import forms

from credentials.models import Credential

from .models import Server


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
        fields = [
            "name",
            "host",
            "port",
            "credential",
            "default_group",
            "is_npu",
        ]
        widgets = {
            "port": forms.NumberInput(attrs={"min": 1, "max": 65535}),
        }
        help_texts = {
            "default_group": "多个分组用英文逗号分隔，如：dev,ops",
            "is_npu": "勾选后，用户申请的分组选择转换为 NPU 算力卡组选择；NPU 卡组由系统自动检测（详情页可重新检测）",
        }
