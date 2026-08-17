from django import forms

from config.forms import PreserveStoredFieldsMixin
from config.widgets import WriteOnlyWidgetMixin

from .models import Credential


class WriteOnlyTextarea(WriteOnlyWidgetMixin, forms.Textarea):
    """接收多行秘密，但任何重渲染都不把原值写回 HTML。"""


class CredentialForm(PreserveStoredFieldsMixin, forms.ModelForm):
    preserved_fields = ("password", "private_key")

    class Meta:
        model = Credential
        fields = ["name", "username", "password", "private_key", "remark"]
        widgets = {
            "password": forms.PasswordInput(render_value=False),
            "private_key": WriteOnlyTextarea(attrs={"rows": 8, "placeholder": "可拖入私钥文件，或直接粘贴内容"}),
            "remark": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_password(self):
        return self.preserved_value("password")

    def clean_private_key(self):
        return self.preserved_value("private_key")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("password") and not cleaned.get("private_key"):
            raise forms.ValidationError("密码与私钥至少填写一项。")
        return cleaned
