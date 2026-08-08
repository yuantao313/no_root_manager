"""Webhook 删除接口：个人中心内嵌的 Webhook 列表使用。

新增/列表内嵌在个人中心页（accounts:profile），此处仅提供删除动作。
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect

from .models import WebhookConfig


@login_required
def webhook_delete(request, pk):
    """删除我的 Webhook（仅本人，404 保护他人数据）。"""
    hook = get_object_or_404(WebhookConfig, pk=pk, owner=request.user)
    hook.delete()
    messages.success(request, f"Webhook「{hook.name}」已删除。")
    return redirect("accounts:profile")
