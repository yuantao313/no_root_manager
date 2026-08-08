from django import forms

from .models import Credential


class CredentialForm(forms.ModelForm):
    class Meta:
        model = Credential
        fields = ["name", "username", "password", "private_key", "remark"]
        widgets = {
            "password": forms.PasswordInput(render_value=True),
            "private_key": forms.Textarea(attrs={"rows": 8, "placeholder": "可拖入私钥文件，或直接粘贴内容"}),
            "remark": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("password") and not cleaned.get("private_key"):
            raise forms.ValidationError("密码与私钥至少填写一项。")
        return cleaned
