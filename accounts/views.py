from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import redirect, render

from .username_gen import generate_username_groups


class ProfileForm(forms.ModelForm):
    """个人资料编辑：允许修改姓名与邮箱（用户名不可改）。"""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]
        labels = {
            "first_name": "名",
            "last_name": "姓",
            "email": "邮箱",
        }
        widgets = {
            "email": forms.EmailInput(attrs={"placeholder": "用于接收开通密码与审批结果"}),
        }


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
    """个人中心：展示并编辑当前用户基本信息。"""
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "个人信息已更新。")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)
    return render(request, "accounts/profile.html", {"user": request.user, "form": form})
