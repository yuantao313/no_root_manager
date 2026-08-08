from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.shortcuts import redirect, render

from notifications.forms import WebhookForm
from notifications.models import WebhookConfig

from .username_gen import generate_username_groups


class ProfileForm(forms.Form):
    """个人资料编辑：姓名（一体化，映射 first_name）+ 邮箱。"""

    name = forms.CharField(label="姓名", max_length=100, required=False)
    email = forms.EmailField(label="邮箱", required=False)

    def __init__(self, *args, **kwargs):
        instance = kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance:
            self.fields["name"].initial = instance.first_name
            self.fields["email"].initial = instance.email

    def save(self, commit=True):
        user = self.instance
        user.first_name = self.cleaned_data["name"].strip()
        user.email = self.cleaned_data["email"].strip()
        if commit:
            user.save()
        return user


def username_suggestions(request):
    """用户名建议接口：根据姓名返回候选用户名（含复姓/单姓分组），无需登录。"""
    name = request.GET.get("name", "").strip()
    data = generate_username_groups(name)
    return JsonResponse(data, json_dumps_params={"ensure_ascii": False})


def register(request):
    """用户注册：所有用户平等注册为普通用户，注册后自动登录。"""
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f"注册成功，欢迎 {user.username}。")
            return redirect("applications:my")
    else:
        form = UserCreationForm()
    return render(request, "accounts/register.html", {"form": form})


@login_required
def profile(request):
    """个人中心：资料正文展示 + 右侧编辑链接；内嵌我的 Webhook 管理（仅管理员）。"""
    hooks = WebhookConfig.objects.filter(owner=request.user)
    editing = request.GET.get("edit") == "1"
    form = ProfileForm(instance=request.user)
    webhook_form = WebhookForm()

    if request.method == "POST":
        # 区分提交来源：保存个人资料 / 添加 Webhook
        if "save_profile" in request.POST:
            form = ProfileForm(request.POST, instance=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, "个人信息已更新。")
                return redirect("accounts:profile")
        elif "add_webhook" in request.POST and request.user.is_staff:
            webhook_form = WebhookForm(request.POST)
            if webhook_form.is_valid():
                hook = webhook_form.save(commit=False)
                hook.owner = request.user
                hook.save()
                messages.success(request, f"Webhook「{hook.name}」已添加。")
                return redirect("accounts:profile")

    return render(
        request,
        "accounts/profile.html",
        {
            "user": request.user,
            "form": form,
            "webhook_form": webhook_form,
            "hooks": hooks,
            "editing": editing,
        },
    )
