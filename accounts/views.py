from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.shortcuts import redirect, render

from .username_gen import generate_username_groups


def is_staff(user):
    return user.is_staff


def username_suggestions(request):
    """用户名建议接口：根据姓名返回候选用户名（含复姓/单姓分组），无需登录。"""
    name = request.GET.get("name", "").strip()
    data = generate_username_groups(name)
    return JsonResponse(data, json_dumps_params={"ensure_ascii": False})


@login_required
@user_passes_test(is_staff)
def register(request):
    """注册（仅限已登录管理员）：创建新的管理员账号。"""
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_staff = True
            user.save()
            messages.success(request, f"管理员账号 {user.username} 已创建。")
            return redirect("applications:list")
    else:
        form = UserCreationForm()
    return render(request, "accounts/register.html", {"form": form})
