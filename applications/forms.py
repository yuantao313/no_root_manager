from django import forms
from django.core.validators import RegexValidator

from servers.models import Server

from .models import Application


class ApplicationForm(forms.ModelForm):
    # 申请理由必填（申请时说明用途）
    description = forms.CharField(
        label="申请理由",
        required=True,
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "请说明申请理由，如需要使用哪些服务、用途等"}),
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
        ]
        labels = {
            "apply_type": "申请类型",
            "target_server": "目标服务器",
            "description": "申请理由",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 目标服务器从数据库服务器表导出下拉选择（select2 可搜索）
        self.fields["target_server"].queryset = Server.objects.all()
        self.fields["target_server"].required = True
        self.fields["target_server"].empty_label = "请选择目标服务器"
        self.fields["target_server"].widget.attrs["class"] = "form-control select2"

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
