from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from django import forms
from django.contrib.auth.forms import PasswordResetForm as BasePasswordResetForm
from django.contrib.auth.forms import SetPasswordMixin, UserCreationForm
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.db import transaction
from django.template.loader import render_to_string

from notifications.services import send_email

from .models import UserProfile

_USERNAME_VALIDATOR = RegexValidator(r"^[A-Za-z0-9_]+\Z", "仅允许字母、数字、下划线")
_USERNAME_HELP_TEXT = "仅限字母/数字/下划线，注册后不可修改，将作为服务器的登录用户名"


def _save_employee_id(user, employee_id):
    UserProfile.objects.update_or_create(
        user=user,
        defaults={"employee_id": employee_id.strip()},
    )


@transaction.atomic
def _save_user_profile(user, employee_id):
    """原子保存用户及其扩展资料。"""
    user.save()
    _save_employee_id(user, employee_id)


class ProfileForm(forms.Form):
    """个人资料编辑：姓名（一体化，映射 first_name）+ 工号 + 邮箱 + 验证码。"""

    name = forms.CharField(label="姓名", max_length=100, required=False)
    employee_id = forms.CharField(label="工号", max_length=50, required=False)
    email = forms.EmailField(label="邮箱", required=False)
    code = forms.CharField(
        label="邮箱验证码", max_length=6, required=False, help_text="修改邮箱时需先点击“发送验证码”并填写"
    )

    def __init__(self, *args, **kwargs):
        instance = kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance:
            self.fields["name"].initial = instance.first_name
            self.fields["employee_id"].initial = getattr(getattr(instance, "profile", None), "employee_id", "") or ""
            self.fields["email"].initial = instance.email

    def save(self, commit=True):
        user = self.instance
        user.first_name = self.cleaned_data["name"].strip()
        user.email = self.cleaned_data["email"].strip()
        if commit:
            _save_user_profile(user, self.cleaned_data["employee_id"])
        return user


class NRMPasswordResetForm(BasePasswordResetForm):
    """密码找回表单：通过系统 SMTP 配置（EmailConfig）发送重置邮件。"""

    def send_mail(
        self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None
    ):
        subject = render_to_string(subject_template_name, context).strip()
        body = render_to_string(email_template_name, context)
        send_email(subject, body, [to_email])


class IdentityFieldsForm(forms.Form):
    """本地注册与 OAuth 注册共用的身份字段。"""

    first_name = forms.CharField(
        label="姓名",
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "真实姓名"}),
    )
    employee_id = forms.CharField(
        label="工号",
        max_length=50,
        widget=forms.TextInput(attrs={"placeholder": "例如 a00123456"}),
    )


class RegisterForm(UserCreationForm, IdentityFieldsForm):
    """注册表单：姓名/工号/邮箱/用户名/密码信息齐全（与 OAuth 补全页一致）。"""

    username = forms.CharField(
        label="用户名",
        max_length=150,
        validators=[_USERNAME_VALIDATOR],
        help_text=_USERNAME_HELP_TEXT,
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    email = forms.EmailField(
        label="邮箱",
        widget=forms.EmailInput(attrs={"placeholder": "用于接收审批通知"}),
    )

    field_order = ["username", "first_name", "employee_id", "email", "password1", "password2"]

    class Meta:
        model = User
        fields = ["username", "first_name", "employee_id", "email", "password1", "password2"]

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data["first_name"].strip()
        user.email = self.cleaned_data["email"].strip()
        if commit:
            _save_user_profile(user, self.cleaned_data["employee_id"])
        return user


class GitCodeSignupForm(SetPasswordMixin, SocialSignupForm, IdentityFieldsForm):
    """GitCode 首次登录创建新账号，并补全本地身份与密码。"""

    password1, password2 = SetPasswordMixin.create_password_fields("密码", "确认密码")
    error_messages = {"password_mismatch": "两次输入的密码不一致。"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 仅邮箱从 OAuth 自动填充；用户名/姓名不预填（避免以 gc<id> 占位身份进入系统）
        self.initial.pop("username", None)
        self.initial.pop("first_name", None)
        self.initial.pop("last_name", None)
        self.fields["email"].required = True
        self.fields["email"].label = "邮箱"
        self.fields["username"].label = "用户名"
        self.fields["username"].help_text = _USERNAME_HELP_TEXT
        self.fields["username"].validators = [_USERNAME_VALIDATOR]
        self.order_fields(["first_name", "employee_id", "username", "email", "password1", "password2"])

    def custom_signup(self, request, user):
        super().custom_signup(request, user)
        employee_id = (self.cleaned_data.get("employee_id") or "").strip()
        if employee_id:
            _save_employee_id(user, employee_id)

    def _post_clean(self):
        super()._post_clean()
        self.validate_passwords()
        self.validate_password_for_user(None, "password1")
