"""个人 Webhook 管理：每个管理员可配置自己的通知 Webhook（仅本人可见可管）。"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import WebhookForm
from .models import WebhookConfig


@login_required
def my_webhooks(request):
    """我的 Webhook：列出并新增（仅本人）。"""
    hooks = WebhookConfig.objects.filter(owner=request.user)
    if request.method == "POST":
        form = WebhookForm(request.POST)
        if form.is_valid():
            hook = form.save(commit=False)
            hook.owner = request.user
            hook.save()
            messages.success(request, f"Webhook「{hook.name}」已添加。")
            return redirect("notifications:my")
    else:
        form = WebhookForm()
    return render(request, "notifications/webhooks.html", {"hooks": hooks, "form": form})


@login_required
def webhook_delete(request, pk):
    """删除我的 Webhook（仅本人，404 保护他人数据）。"""
    hook = get_object_or_404(WebhookConfig, pk=pk, owner=request.user)
    hook.delete()
    messages.success(request, f"Webhook「{hook.name}」已删除。")
    return redirect("notifications:my")
