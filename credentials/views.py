from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CredentialForm
from .models import Credential


def is_staff(user):
    return user.is_staff


@login_required
@user_passes_test(is_staff)
def credential_list(request):
    """凭据列表（仅管理员）。"""
    credentials = Credential.objects.all()
    return render(request, "credentials/list.html", {"credentials": credentials})


@login_required
@user_passes_test(is_staff)
def credential_create(request):
    """新增凭据（仅管理员）。"""
    if request.method == "POST":
        form = CredentialForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "凭据已添加。")
            return redirect("credentials:list")
    else:
        form = CredentialForm()
    return render(request, "credentials/form.html", {"form": form})


@login_required
@user_passes_test(is_staff)
def credential_detail(request, pk):
    """凭据详情（仅管理员），展示掩码信息。"""
    credential = get_object_or_404(Credential, pk=pk)
    return render(request, "credentials/detail.html", {"credential": credential})
