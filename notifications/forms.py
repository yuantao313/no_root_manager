from django import forms

from .models import WebhookConfig


class WebhookForm(forms.ModelForm):
    """个人 Webhook 表单（owner 由视图填充）。"""

    class Meta:
        model = WebhookConfig
        fields = ["name", "url", "secret", "enabled"]
        labels = {
            "name": "名称",
            "url": "Webhook URL",
            "secret": "密钥（可选）",
            "enabled": "启用",
        }
        widgets = {
            "secret": forms.PasswordInput(attrs={"placeholder": "留空则不设置"}),
        }
