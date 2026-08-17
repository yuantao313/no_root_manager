from django import forms

from .models import WebhookConfig
from .security import UnsafeWebhookURL, validate_webhook_url


class WriteOnlyURLInput(forms.URLInput):
    """Webhook URL 常自带访问令牌，任何重渲染都不回填 HTML。"""

    def format_value(self, value):  # noqa: ARG002
        return ""


class WebhookForm(forms.ModelForm):
    """个人 Webhook 表单（owner 由视图填充）。"""

    class Meta:
        model = WebhookConfig
        fields = ["name", "url", "secret", "enabled"]
        labels = {
            "name": "平台",
            "url": "Webhook URL",
            "secret": "密钥（可选）",
            "enabled": "启用",
        }
        widgets = {
            "name": forms.Select(attrs={"class": "form-control"}),
            "url": WriteOnlyURLInput(attrs={"placeholder": "新建时必填；已有配置留空则保持原 URL"}),
            "secret": forms.PasswordInput(attrs={"placeholder": "留空则保留现有密钥"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["url"].required = not bool(self.instance and self.instance.pk)
        # 机器人 URL 往往自带访问令牌，编辑时也按秘密处理，绝不回填 HTML。
        if self.instance and self.instance.pk:
            self.initial["url"] = ""

    def clean_url(self):
        value = (self.cleaned_data.get("url") or "").strip()
        if not value and self.instance and self.instance.pk:
            return self.instance.url
        if not value:
            raise forms.ValidationError("请填写 Webhook URL。")
        try:
            return validate_webhook_url(value)
        except UnsafeWebhookURL as exc:
            raise forms.ValidationError(str(exc)) from exc
