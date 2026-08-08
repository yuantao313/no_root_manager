from django import forms

from servers.models import Server

from .models import Application


class ApplicationForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = [
            "applicant_name",
            "username",
            "email",
            "employee_id",
            "apply_type",
            "target_server",
            "title",
            "description",
            "valid_until",
            "needs_sudo",
            "migrate_from_dir",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "valid_until": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }
        labels = {
            "applicant_name": "姓名",
            "username": "用户名",
            "email": "邮箱",
            "employee_id": "工号",
            "apply_type": "申请类型",
            "target_server": "目标服务器",
            "title": "申请标题",
            "description": "申请内容",
            "valid_until": "使用截止时间",
            "needs_sudo": "同时申请 root/sudo 权限",
            "migrate_from_dir": "迁移已有目录",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 目标服务器从数据库服务器表导出下拉选择
        self.fields["target_server"].queryset = Server.objects.all()
        self.fields["target_server"].required = False
        self.fields["target_server"].empty_label = "（可选）"
