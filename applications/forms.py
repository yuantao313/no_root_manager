from django import forms

from servers.models import Server

from .models import Application


class DynamicMultipleChoiceField(forms.MultipleChoiceField):
    """选项由前端动态加载的多选框：choices 为空时不校验可用性，
    合法性由业务层（clean_applied_groups）按所选服务器校验。
    """

    def valid_value(self, value):
        if not self.choices:
            return True  # 选项由前端动态加载，跳过 Django 可用性校验
        return super().valid_value(value)


class ApplicationForm(forms.ModelForm):
    # NPU 卡组多选框：仅 NPU 服务器提供，选项由前端根据所选服务器动态填充（npu + npuN）
    applied_groups = DynamicMultipleChoiceField(
        required=False,
        label="NPU 卡组（可选）",
        help_text="NPU 服务器可选择授权使用的算力卡组",
        widget=forms.CheckboxSelectMultiple,
    )
    # 转移类型：指定目标机器上已有的用户名（选择式输入，带提示）
    transfer_username = forms.CharField(
        required=False,
        label="已有机器用户名",
        help_text="选择“转移已有账号为受管用户”时填写目标机器上已存在的用户名",
        widget=forms.TextInput(attrs={"placeholder": "如：john"}),
    )

    class Meta:
        model = Application
        # 用户名/工号/姓名不再手填：从账号（User.username/UserProfile/姓名）自动带入
        fields = [
            "apply_type",
            "target_server",
            "description",
            "valid_until",
            "needs_sudo",
            "applied_groups",
        ]
        widgets = {
            "description": forms.Textarea(
                attrs={"rows": 5, "placeholder": "请说明申请理由，如需要使用哪些服务、用途等"}
            ),
            "valid_until": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }
        labels = {
            "apply_type": "申请类型",
            "target_server": "目标服务器",
            "description": "申请理由",
            "valid_until": "使用截止时间",
            "needs_sudo": "同时申请 root/sudo 权限",
            "applied_groups": "NPU 卡组（可选）",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 目标服务器从数据库服务器表导出下拉选择
        self.fields["target_server"].queryset = Server.objects.all()
        self.fields["target_server"].required = False
        self.fields["target_server"].empty_label = "（可选）"
        # 编辑回显：已有分组值作为初始选中项（前端 JS 会重建选项）
        if self.instance and self.instance.applied_groups:
            initial = [g.strip() for g in self.instance.applied_groups.split(",") if g.strip()]
            self.fields["applied_groups"].initial = initial

    def clean_applied_groups(self):
        """校验勾选的分组：仅 NPU 服务器可选 NPU 卡组，普通服务器不可选分组。"""
        groups = self.cleaned_data.get("applied_groups") or []
        server = self.cleaned_data.get("target_server")
        if not groups:
            return ",".join([])
        if server is None:
            raise forms.ValidationError("请先选择目标服务器。")
        if not server.is_npu:
            raise forms.ValidationError("该服务器不支持分组选择（仅 NPU 服务器可选 NPU 卡组）。")
        allowed = server.npu_groups_list()
        invalid = [g for g in groups if g not in allowed]
        if invalid:
            raise forms.ValidationError(f"卡组 {', '.join(invalid)} 不属于所选服务器。")
        return ",".join(groups)
