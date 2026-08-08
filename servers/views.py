from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from config.decorators import superuser_required

from .forms import ServerForm
from .management import ensure_nrm_group, sync_managed_users, take_over_user
from .models import Server
from .ssh import test_server_connection


def server_groups_api(request, pk):
    """返回服务器的分组配置（默认分组 + 可附加分组），供申请表单前端联动。"""
    server = get_object_or_404(Server, pk=pk)
    return JsonResponse(
        {
            "id": server.pk,
            "default_groups": server.default_groups_list(),
            "extra_groups": server.extra_groups_list(),
        },
        json_dumps_params={"ensure_ascii": False},
    )


@superuser_required
def server_list(request):
    """服务器列表（仅超级管理员）。"""
    servers = Server.objects.all()
    return render(request, "servers/list.html", {"servers": servers})


@superuser_required
def server_create(request):
    """新增服务器（仅超级管理员）。选择“保存并测试连接”时先测试连通性，通过后才保存。"""
    if request.method == "POST":
        form = ServerForm(request.POST)
        action = request.POST.get("action", "save")
        if form.is_valid():
            server = form.save(commit=False)
            test_msg = ""
            if action == "test":
                ok, test_msg = test_server_connection(server)
                if not ok:
                    messages.error(request, f"连接测试未通过，未保存：{test_msg}")
                    return render(request, "servers/form.html", {"form": form, "editing": False})
            server.save()
            form.save_m2m()
            messages.success(request, "服务器已添加。" + (test_msg or ""))
            return redirect("servers:list")
    else:
        form = ServerForm()
    return render(request, "servers/form.html", {"form": form, "editing": False})


@superuser_required
def server_edit(request, pk):
    """编辑服务器（仅超级管理员）：可修改基本信息与分组配置。"""
    server = get_object_or_404(Server, pk=pk)
    if request.method == "POST":
        form = ServerForm(request.POST, instance=server)
        action = request.POST.get("action", "save")
        if form.is_valid():
            server = form.save(commit=False)
            test_msg = ""
            if action == "test":
                ok, test_msg = test_server_connection(server)
                if not ok:
                    messages.error(request, f"连接测试未通过，未保存：{test_msg}")
                    return render(request, "servers/form.html", {"form": form, "editing": True})
            server.save()
            form.save_m2m()
            messages.success(request, "服务器已更新。" + (test_msg or ""))
            return redirect("servers:detail", pk=server.pk)
    else:
        form = ServerForm(instance=server)
    return render(request, "servers/form.html", {"form": form, "editing": True})


@superuser_required
def server_test(request, pk):
    """对已保存的服务器执行连接测试（仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    if not server.credential:
        messages.error(request, "该服务器未关联凭据，无法测试。")
        return redirect("servers:detail", pk=pk)
    ok, msg = test_server_connection(server)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_detail(request, pk):
    """服务器详情（仅超级管理员），含受管用户列表。"""
    server = get_object_or_404(Server, pk=pk)
    return render(request, "servers/detail.html", {"server": server})


@superuser_required
def server_sync_users(request, pk):
    """同步目标机器 nrm_managed 组成员到数据库（仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    if not server.credential:
        messages.error(request, "该服务器未关联凭据，无法同步。")
        return redirect("servers:detail", pk=pk)
    ok, msg = ensure_nrm_group(server)
    if not ok:
        messages.error(request, msg)
        return redirect("servers:detail", pk=pk)
    ok, msg = sync_managed_users(server)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_takeover_user(request, pk):
    """将指定用户加入目标机器 nrm_managed 组（接管，仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    if request.method != "POST" or not username:
        messages.error(request, "请填写要接管的用户名。")
        return redirect("servers:detail", pk=pk)
    ok, msg = take_over_user(server, username)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("servers:detail", pk=pk)
