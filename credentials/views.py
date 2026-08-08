from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from config.decorators import staff_required, superuser_required
from servers.models import Server

from .forms import CredentialForm
from .models import Credential


def _visible_credentials(user):
    """用户可见的凭据：超级管理员全部，普通管理员仅绑定服务器的凭据。"""
    if user.is_superuser:
        return Credential.objects.all()
    return Credential.objects.filter(servers__in=Server.visible_to(user)).distinct()


@staff_required
def credential_list(request):
    """凭据列表（管理员）：超级管理员全部，普通管理员仅绑定服务器的凭据。"""
    credentials = _visible_credentials(request.user)
    return render(request, "credentials/list.html", {"credentials": credentials})


@superuser_required
def credential_create(request):
    """新增凭据（仅超级管理员，凭据为敏感全局资源）。"""
    if request.method == "POST":
        form = CredentialForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "凭据已添加。")
            return redirect("credentials:list")
    else:
        form = CredentialForm()
    return render(request, "credentials/form.html", {"form": form})


@staff_required
def credential_detail(request, pk):
    """凭据详情（管理员，需有权限），展示掩码信息。"""
    credential = get_object_or_404(_visible_credentials(request.user), pk=pk)
    return render(request, "credentials/detail.html", {"credential": credential})
