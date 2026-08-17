from django import forms
from django.core.validators import RegexValidator
from django.urls import reverse

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
    # 申请理由必填（申请时说明用途）
    description = forms.CharField(
        label="申请理由",
        required=True,
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "请说明申请理由，如需要使用哪些服务、用途等"}),
    )
    # NPU 卡组多选框：仅 NPU 服务器提供，选项由前端根据所选服务器动态填充（npu + npuN）
    applied_groups = DynamicMultipleChoiceField(
        required=False,
        label="NPU 卡组（可选）",
        help_text="NPU 服务器可选择授权使用的算力卡组",
        widget=forms.CheckboxSelectMultiple(attrs={"class": "nrm-checkbox"}),
    )
    # 转移类型不枚举目标机账号，申请人只填写自己已知的用户名。
    transfer_username = forms.CharField(
        required=False,
        label="已有机器用户名",
        max_length=32,
        validators=[RegexValidator(r"^[a-z_][a-z0-9_-]{0,31}$", "请输入合法的 Linux 用户名。")],
        help_text="请输入你本人在目标服务器上已有的用户名。",
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    # sudo / docker 均可取得 root 级能力，只能由超级管理员审批。
    user_groups = forms.MultipleChoiceField(
        required=False,
        label="申请高危权限组",
        help_text="sudo 与 docker 均具有 root 级能力，只能由超级管理员审批。",
        choices=[(g, g) for g in Application.USER_GROUP_CHOICES],
        widget=forms.CheckboxSelectMultiple(attrs={"class": "nrm-checkbox"}),
    )

    class Meta:
        model = Application
        # 用户名/工号/姓名不再手填：从账号（User.username/UserProfile/姓名）自动带入
        fields = [
            "apply_type",
            "target_server",
            "description",
            "applied_groups",
        ]
        labels = {
            "apply_type": "申请类型",
            "target_server": "目标服务器",
            "description": "申请理由",
            "applied_groups": "NPU 卡组（可选）",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 目标服务器从数据库服务器表导出下拉选择（select2 可搜索）
        self.fields["target_server"].queryset = Server.objects.all()
        self.fields["target_server"].required = True
        self.fields["target_server"].empty_label = "请选择目标服务器"
        # 前端静态化：groups API 地址经 data-* 传给 app.js（JS 不写模板标签）
        self.fields["target_server"].widget.attrs["class"] = "form-control select2"
        self.fields["target_server"].widget.attrs["data-groups-url"] = reverse("servers:groups_api", args=[0])
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

    def clean(self):
        cleaned_data = super().clean()
        apply_type = cleaned_data.get("apply_type")
        user_groups = cleaned_data.get("user_groups") or []
        if apply_type == Application.ApplyType.GROUP and not user_groups:
            self.add_error("user_groups", "申请用户组时请至少选择一个权限组。")
        elif apply_type != Application.ApplyType.GROUP:
            # 隐藏字段也必须由后端归一化，防止给其他申请类型夹带无效权限组。
            cleaned_data["user_groups"] = []
        return cleaned_data


class ApplicationReviewForm(forms.Form):
    """审批输入校验：通过意见可选，驳回必须说明原因。"""

    comment = forms.CharField(required=False, max_length=500, strip=True)

    def __init__(self, *args, require_comment=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_comment = require_comment

    def clean_comment(self):
        comment = self.cleaned_data["comment"]
        if self.require_comment and not comment:
            raise forms.ValidationError("驳回申请时请填写简短意见。")
        return comment
