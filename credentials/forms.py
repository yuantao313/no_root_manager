from django import forms

from .models import Credential


class WriteOnlyTextarea(forms.Textarea):
    """接收多行秘密，但任何重渲染都不把原值写回 HTML。"""

    def format_value(self, value):  # noqa: ARG002
        return ""


class CredentialForm(forms.ModelForm):
    class Meta:
        model = Credential
        fields = ["name", "username", "password", "private_key", "remark"]
        widgets = {
            "password": forms.PasswordInput(render_value=False),
            "private_key": WriteOnlyTextarea(attrs={"rows": 8, "placeholder": "可拖入私钥文件，或直接粘贴内容"}),
            "remark": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stored_password = self.instance.password if self.instance.pk else ""
        self._stored_private_key = self.instance.private_key if self.instance.pk else ""

    def clean_password(self):
        return self.cleaned_data.get("password") or self._stored_password

    def clean_private_key(self):
        return self.cleaned_data.get("private_key") or self._stored_private_key

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("password") and not cleaned.get("private_key"):
            raise forms.ValidationError("密码与私钥至少填写一项。")
        return cleaned
