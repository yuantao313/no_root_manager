from django.contrib import messages
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from config.decorators import superuser_required

from .forms import ServerForm
from .management import (
    clear_managed_users_cache,
    clear_npu_state_cache,
    detect_npu_groups,
    ensure_nrm_group,
    get_managed_users_cached,
    get_npu_state_cached,
    list_system_users,
    lock_user,
    run_init_script,
    take_over_user,
    unlock_user,
)
from .models import MachineUserBinding, Server
from .ssh import test_server_connection


def server_groups_api(request, pk):
    """返回服务器的分组配置与机器用户列表，供申请表单前端联动。

    - extra_groups：NPU 卡组（npu + npuN），仅 NPU 服务器（读内存缓存，不 SSH 卡顿）
    - users：目标机器可接管的用户列表（/etc/passwd uid≥1000），供转移类型下拉
    """
    server = get_object_or_404(Server, pk=pk)
    extra = get_npu_state_cached(server)["groups"] if server.is_npu else []
    users = []
    if server.credential:
        try:
            ok, users, _ = list_system_users(server)
            if not ok:
                users = []
        except Exception:  # noqa: BLE001 —— SSH 不可达时降级为空列表
            users = []
    return JsonResponse(
        {
            "id": server.pk,
            "default_groups": server.default_groups_list(),
            "extra_groups": extra,
            "is_npu": server.is_npu,
            "users": users,
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
    """服务器详情（仅超级管理员），含受管用户列表与可接管用户候选。

    受管用户实时扫描目标机并缓存到内存（不落库）。
    """
    server = get_object_or_404(Server, pk=pk)
    available_users = []
    managed_members, _ = [], ""
    if server.credential:
        try:
            ok, available_users, _ = list_system_users(server)
            if not ok:
                available_users = []
            # 受管用户（内存缓存）
            managed_members, _ = get_managed_users_cached(server)
        except Exception:  # noqa: BLE001 —— SSH 不可达时降级为空列表
            available_users = []
            managed_members = []
    return render(
        request,
        "servers/detail.html",
        {
            "server": server,
            "available_users": available_users,
            "managed_users": managed_members,
            # 手动接管时可选绑定的平台用户（下拉）
            "sys_users": User.objects.order_by("username"),
        },
    )


@superuser_required
def server_sync_users(request, pk):
    """刷新目标机器状态（仅超级管理员）：清空内存缓存并重新扫描受管用户。"""
    server = get_object_or_404(Server, pk=pk)
    if not server.credential:
        messages.error(request, "该服务器未关联凭据，无法同步。")
        return redirect("servers:detail", pk=pk)
    ok, msg = ensure_nrm_group(server)
    if not ok:
        messages.error(request, msg)
        return redirect("servers:detail", pk=pk)
    # 强制刷新内存缓存
    clear_managed_users_cache(server)
    members, scan_msg = get_managed_users_cached(server, force_refresh=True)
    messages.success(request, f"已刷新：扫描到 {len(members)} 个受管用户（{scan_msg}）")
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_takeover_user(request, pk):
    """将指定用户加入目标机器 nrm_managed 组（接管，仅超级管理员）。

    可选绑定一个平台用户（机器受管用户 ↔ 系统用户归属，写入 MachineUserBinding）。
    """
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    if request.method != "POST" or not username:
        messages.error(request, "请填写要接管的用户名。")
        return redirect("servers:detail", pk=pk)
    ok, msg = take_over_user(server, username)
    if ok:
        # 可选绑定平台用户：归属关系落库（source=manual），唯一约束防重复
        bind_id = request.POST.get("bind_user_id", "").strip()
        MachineUserBinding.objects.update_or_create(
            server=server,
            username=username,
            defaults={
                "user_id": int(bind_id) if bind_id.isdigit() else None,
                "source": "manual",
            },
        )
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_lock_user(request, pk):
    """禁用目标机器用户（passwd -l，仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    if request.method != "POST" or not username:
        messages.error(request, "参数错误。")
        return redirect("servers:detail", pk=pk)
    ok, msg = lock_user(server, username)
    messages.success(request, msg) if ok else messages.error(request, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_unlock_user(request, pk):
    """启用目标机器用户（passwd -u，仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    if request.method != "POST" or not username:
        messages.error(request, "参数错误。")
        return redirect("servers:detail", pk=pk)
    ok, msg = unlock_user(server, username)
    messages.success(request, msg) if ok else messages.error(request, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_run_init(request, pk):
    """执行服务器初始化脚本（远程 get 并在目标机运行，仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    if request.method != "POST":
        return redirect("servers:detail", pk=pk)
    ok, msg = run_init_script(server)
    messages.success(request, msg) if ok else messages.error(request, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_configure_npu(request, pk):
    """NPU 服务器：检测目标机 NPU 卡组并保存（仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    if request.method != "POST":
        return redirect("servers:detail", pk=pk)
    ok, groups, msg = detect_npu_groups(server)
    if ok:
        server.is_npu = True
        server.npu_groups = ",".join(groups)
        server.save(update_fields=["is_npu", "npu_groups"])
        # 同步内存缓存，申请界面直接读缓存不卡顿
        get_npu_state_cached(server, force_refresh=True)
        messages.success(request, f"NPU 检测完成并保存：{msg}")
    else:
        clear_npu_state_cache(server)
        messages.error(request, f"NPU 检测失败：{msg}")
    return redirect("servers:detail", pk=pk)
