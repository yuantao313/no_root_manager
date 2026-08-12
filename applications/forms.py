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


class DynamicChoiceField(forms.ChoiceField):
    """选项由前端动态加载的下拉：choices 为空时不校验可用性。"""

    def valid_value(self, value):
        if not self.choices:
            return True  # 选项由前端动态加载，跳过 Django 可用性校验
        return super().valid_value(value)


class ApplicationForm(forms.ModelForm):
    # 申请理由必填（申请时说明用途）
    description = forms.CharField(
        label="申请理由",
        required=True,
        widget=forms.Textarea(
            attrs={"rows": 5, "placeholder": "请说明申请理由，如需要使用哪些服务、用途等"}
        ),
    )
    # NPU 卡组多选框：仅 NPU 服务器提供，选项由前端根据所选服务器动态填充（npu + npuN）
    applied_groups = DynamicMultipleChoiceField(
        required=False,
        label="NPU 卡组（可选）",
        help_text="NPU 服务器可选择授权使用的算力卡组",
        widget=forms.CheckboxSelectMultiple,
    )
    # 转移类型：目标机器已有用户名（由前端从机器读取，下拉选择）
    transfer_username = DynamicChoiceField(
        required=False,
        label="已有机器用户名",
        help_text="从目标机器读取的用户列表中选择要接管的账号",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    # 申请的用户组（用户组类型必选）：sudo / docker / HwHiAiUser
    user_groups = forms.MultipleChoiceField(
        required=False,
        label="申请用户组",
        help_text="申请加入所选用户组（可多选）",
        choices=[(g, g) for g in Application.USER_GROUP_CHOICES],
        widget=forms.CheckboxSelectMultiple,
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
        # 目标服务器从数据库服务器表导出下拉选择（select2 可搜索）
        self.fields["target_server"].queryset = Server.objects.all()
        self.fields["target_server"].required = False
        self.fields["target_server"].empty_label = "（可选）"
        self.fields["target_server"].widget.attrs["class"] = "form-control select2"
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
