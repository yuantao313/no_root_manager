from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from config.decorators import superuser_required

from .devices import clear_device_info_cache, get_device_info, get_device_info_cached
from .forms import ServerForm
from .management import (
    NRM_GROUP,
    add_user_group,
    clear_managed_users_cache,
    clear_npu_state_cache,
    clear_user_groups_cache,
    detect_npu_groups,
    ensure_nrm_group,
    get_managed_users_cached,
    get_npu_state_cached,
    get_user_groups_cached,
    list_system_users,
    lock_user,
    remove_user_group,
    run_init_script,
    sort_user_groups,
    take_over_user,
    unlock_user,
)
from .models import MachineUserBinding, Server
from .ssh import test_server_connection


def server_groups_api(request, pk):
    """返回服务器的分组配置、设备信息与机器用户列表，供申请表单前端联动。

    - extra_groups：NPU 卡组（npu + npuN），仅 NPU 服务器（读内存缓存，不 SSH 卡顿）
    - device：设备信息（CPU/内存/硬盘/NPU 卡型号），走 get_device_info 的 TTL 缓存
      + 数据库快照回退，目标机不可达时仍展示最近一次成功采集的数据
    - users：目标机器可接管的用户列表（/etc/passwd uid≥1000），供转移类型下拉
    单接口一次返回，前端一次 fetch 即可渲染卡组按钮与设备信息，避免多次请求。
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
    try:
        # 申请页只读模式：绝不实时 SSH（SSH 错误永不透出到申请页），
        # 返回内存缓存/数据库快照；设备信息在详情页手动刷新时才实时采集
        device = get_device_info_cached(server)
    except Exception:  # noqa: BLE001 —— 设备查询失败不影响分组接口
        device = {"npu": [], "gpu": [], "cpu": "", "memory": "", "disk": "", "msg": ""}
    return JsonResponse(
        {
            "id": server.pk,
            "default_groups": server.default_groups_list(),
            "extra_groups": extra,
            "is_npu": server.is_npu,
            "users": users,
            "device": device,
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


@login_required
def server_device_api(request, pk):
    """设备信息 API（登录用户）：供详情页与申请页前端异步 fetch 填充。

    走 get_device_info 的 TTL 缓存；目标机不可达时回退数据库快照（页面不空白）。
    设备信息仅含硬件信息（CPU/内存/硬盘/NPU 型号），不暴露凭据，登录用户可读。
    """
    server = get_object_or_404(Server, pk=pk)
    try:
        device = get_device_info(server)
    except Exception:  # noqa: BLE001 —— 设备查询失败不影响页面
        device = {"npu": [], "gpu": [], "cpu": "", "memory": "", "disk": "", "msg": "设备信息获取失败"}
    return JsonResponse(device, json_dumps_params={"ensure_ascii": False})


@superuser_required
def server_detail(request, pk):
    """服务器详情（仅超级管理员），含受管用户列表与可接管用户候选。

    受管用户实时扫描目标机并缓存到内存（不落库）；设备信息不再同步查询
    （避免 SSH 阻塞页面渲染），由前端经 device_api 异步 fetch + 加载中提示填充。
    """
    server = get_object_or_404(Server, pk=pk)
    available_users = []
    managed_users = []
    if server.credential:
        try:
            ok, available_users, _ = list_system_users(server)
            if not ok:
                available_users = []
            # 受管用户（内存缓存）→ 关联归属平台用户（MachineUserBinding）
            managed_members, _ = get_managed_users_cached(server)
            bindings = {
                b.username: b.user
                for b in MachineUserBinding.objects.filter(server=server, username__in=managed_members)
            }
            managed_users = [
                {"username": u, "user": bindings.get(u)} for u in managed_members
            ]
            # 用户所属组（内存缓存批量查询，失败不影响列表展示，组信息可为空）
            try:
                groups_map = get_user_groups_cached(server, managed_members)
                npu_names = set(server.npu_groups_list())
                for item in managed_users:
                    groups = groups_map.get(item["username"], [])
                    priority, npu_in, others = sort_user_groups(item["username"], groups, npu_names)
                    item["groups_priority"] = priority
                    item["groups_npu"] = npu_in
                    item["groups_other"] = others
            except Exception:  # noqa: BLE001 —— 组查询失败降级为空组展示
                for item in managed_users:
                    item["groups_priority"] = []
                    item["groups_npu"] = []
                    item["groups_other"] = []
        except Exception:  # noqa: BLE001 —— SSH 不可达时降级为空列表
            available_users = []
            managed_users = []
    return render(
        request,
        "servers/detail.html",
        {
            "server": server,
            "available_users": available_users,
            # 受管用户：机器用户名 + 归属系统用户（dict 列表，模板按 item.username/item.user 渲染）
            "managed_users": managed_users,
            # NPU 卡组全量（按钮灯渲染用）：仅 NPU 服务器有值
            "npu_group_names": server.npu_groups_list(),
            # 手动接管时可选绑定的平台用户（下拉）
            "sys_users": User.objects.order_by("username"),
        },
    )


@superuser_required
def server_sync_users(request, pk):
    """刷新目标机器状态（仅超级管理员）：清空受管用户与设备信息缓存并重新扫描。"""
    server = get_object_or_404(Server, pk=pk)
    if not server.credential:
        messages.error(request, "该服务器未关联凭据，无法同步。")
        return redirect("servers:detail", pk=pk)
    ok, msg = ensure_nrm_group(server)
    if not ok:
        messages.error(request, msg)
        return redirect("servers:detail", pk=pk)
    # 强制刷新内存缓存（受管用户 + 设备信息）
    clear_managed_users_cache(server)
    clear_device_info_cache()
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
def server_add_user_group(request, pk):
    """将受管用户加入指定用户组（usermod -aG，组不存在自动创建，仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    group = request.POST.get("group", "").strip()
    if request.method != "POST" or not username or not group:
        messages.error(request, "参数错误。")
        return redirect("servers:detail", pk=pk)
    ok, msg = add_user_group(server, username, group)
    if ok:
        clear_user_groups_cache(server)
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_remove_user_group(request, pk):
    """将受管用户从指定用户组移除（仅超级管理员；nrm_managed 标识组不可移除）。"""
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    group = request.POST.get("group", "").strip()
    if request.method != "POST" or not username or not group:
        messages.error(request, "参数错误。")
        return redirect("servers:detail", pk=pk)
    if group == NRM_GROUP:
        messages.error(request, f"{NRM_GROUP} 是受管用户标识组，不能直接移除。")
        return redirect("servers:detail", pk=pk)
    ok, msg = remove_user_group(server, username, group)
    if ok:
        clear_user_groups_cache(server)
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_update_user_groups(request, pk):
    """按目标组全集更新受管用户所属组（仅超级管理员）。

    详情页按钮灯编辑后一次性提交目标组全集（逗号分隔）；后端对比当前组
    计算加入/移出差异并批量执行，nrm_managed 标识组强制保留不可移除。
    """
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    if request.method != "POST" or not username:
        messages.error(request, "参数错误。")
        return redirect("servers:detail", pk=pk)
    target = {g.strip() for g in request.POST.get("groups", "").split(",") if g.strip()}
    target.add(NRM_GROUP)  # 受管标识组强制保留
    current = set(get_user_groups_cached(server, [username]).get(username, []))
    to_add = target - current
    to_remove = current - target
    ok_all = True
    for g in sorted(to_add):
        ok, msg = add_user_group(server, username, g)
        if not ok:
            ok_all = False
            messages.error(request, msg)
    for g in sorted(to_remove):
        if g == NRM_GROUP:
            continue
        ok, msg = remove_user_group(server, username, g)
        if not ok:
            ok_all = False
            messages.error(request, msg)
    clear_user_groups_cache(server)
    if ok_all:
        messages.success(request, f"已更新 {username} 的用户组配置。")
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
