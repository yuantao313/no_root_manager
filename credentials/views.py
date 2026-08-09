from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from config.decorators import superuser_required

from .forms import CredentialForm
from .models import Credential


@superuser_required
def credential_list(request):
    """凭据列表（仅超级管理员，凭据为敏感全局资源）。"""
    credentials = Credential.objects.all()
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


@superuser_required
def credential_detail(request, pk):
    """凭据详情（仅超级管理员），展示掩码信息。"""
    credential = get_object_or_404(Credential, pk=pk)
    return render(request, "credentials/detail.html", {"credential": credential})


@superuser_required
def credential_delete(request, pk):
    """删除凭据（仅超级管理员）。

    若凭据被服务器引用（Server.credential 外键，on_delete=PROTECT），
    阻止删除并提示关联服务器数量，引导去服务器管理解除。
    """
    credential = get_object_or_404(Credential, pk=pk)
    if request.method == "POST":
        linked_count = credential.servers.count()
        if linked_count > 0:
            messages.error(
                request,
                f"该凭据被 {linked_count} 个服务器使用，无法删除。请先到「服务器」管理中删除/更换使用该凭据的服务器。",
            )
            return redirect("credentials:list")
        credential.delete()
        messages.success(request, f"凭据「{credential.name}」已删除。")
        return redirect("credentials:list")
    return redirect("credentials:list")
