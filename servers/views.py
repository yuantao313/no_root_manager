import re

from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from applications.models import Application
from config.decorators import superuser_required
from credentials.models import Credential
from notifications.services import send_machine_password_reset

from .devices import clear_device_info_cache, get_device_info
from .forms import ServerForm
from .management import (
    NRM_GROUP,
    add_user_group,
    clear_managed_users_cache,
    clear_user_groups_cache,
    get_managed_users_cached,
    get_user_groups_cached,
    list_system_users,
    remove_user_group,
    reset_user_password,
    run_init_script,
    set_user_groups,
    sort_user_groups,
    take_over_user,
    toggle_user_lock,
)
from .models import MachineUserBinding, Server
from .ssh import test_server_connection


def _message_result(request, ok, message):
    (messages.success if ok else messages.error)(request, message)


def _candidate_fingerprint(message):
    """从首次只读探测结果提取候选指纹，仅供页面填入，绝不自动保存。"""
    match = re.search(r"SHA256:[A-Za-z0-9+/]+={0,2}", message or "")
    return match.group(0) if match else ""


@superuser_required
def server_list(request):
    """服务器与凭据的统一管理入口（仅超级管理员）。"""
    active_tab = request.GET.get("tab", "servers")
    if active_tab not in {"servers", "credentials"}:
        active_tab = "servers"
    servers = Server.objects.select_related("credential")
    credentials = Credential.objects.only("pk", "name", "username", "updated_at").annotate(
        server_count=Count("servers")
    )
    return render(
        request,
        "servers/list.html",
        {
            "servers": servers,
            "credentials": credentials,
            "active_tab": active_tab,
        },
    )


@superuser_required
def server_create(request):
    """新增服务器（仅超级管理员）。选择“保存并测试连接”时先测试连通性，通过后才保存。"""
    return _server_form(request)


@superuser_required
def server_edit(request, pk):
    """编辑服务器（仅超级管理员）：可修改基本信息与分组配置。"""
    return _server_form(request, get_object_or_404(Server, pk=pk))


def _server_form(request, server=None):
    """服务器新增/编辑共用流程，保留保存前连接测试。"""
    editing = server is not None
    form = ServerForm(request.POST or None, instance=server)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if form.is_valid():
            server = form.save(commit=False)
            test_msg = ""
            if action == "test":
                ok, test_msg = test_server_connection(server)
                if not ok:
                    messages.error(request, f"连接测试未通过，未保存：{test_msg}")
                    return render(
                        request,
                        "servers/form.html",
                        {
                            "form": form,
                            "editing": editing,
                            "candidate_fingerprint": _candidate_fingerprint(test_msg),
                        },
                    )
            server.save()
            form.save_m2m()
            if editing:
                # 地址、凭据或主机信息可能已变化，不能继续复用旧目标机快照。
                clear_managed_users_cache(server)
                clear_user_groups_cache(server)
                clear_device_info_cache(server)
            messages.success(request, ("服务器已更新。" if editing else "服务器已添加。") + (test_msg or ""))
            return redirect("servers:detail", pk=server.pk) if editing else redirect("servers:list")
    return render(request, "servers/form.html", {"form": form, "editing": editing})


@superuser_required
@require_POST
def server_test(request, pk):
    """对已保存的服务器执行连接测试（仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    if not server.credential:
        messages.error(request, "该服务器未关联凭据，无法测试。")
        return redirect("servers:detail", pk=pk)
    ok, msg = test_server_connection(server)
    _message_result(request, ok, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
def server_device_api(request, pk):
    """设备信息 API（仅超级管理员）：供服务器详情页异步 fetch 填充。

    走 get_device_info 的 TTL 缓存；目标机不可达时回退数据库快照（页面不空白）。
    该入口可能触发实时 SSH 探测，不向普通用户开放，避免越权探测或被滥用放大连接压力。
    """
    server = get_object_or_404(Server, pk=pk)
    try:
        device = get_device_info(server)
    except Exception:  # noqa: BLE001 —— 设备查询失败不影响页面
        device = {"cpu": "", "memory": "", "disk": "", "msg": "设备信息获取失败"}
    return JsonResponse(device, json_dumps_params={"ensure_ascii": False})


def _server_user_context(server):
    """构建用户管理局部模板上下文；远程数据统一由单次快照读取。"""
    available_users = []
    managed_users = []
    scan_error = ""
    if server.credential:
        try:
            ok, available_users, scan_message = list_system_users(server)
            if not ok:
                available_users = []
                scan_error = scan_message
            # 受管用户（内存缓存）→ 关联归属平台用户（MachineUserBinding）
            managed_members, _ = get_managed_users_cached(server)
            bindings = {
                b.username: b.user
                for b in MachineUserBinding.objects.filter(server=server, username__in=managed_members)
            }
            managed_users = [{"username": u, "user": bindings.get(u)} for u in managed_members]
            # 用户所属组（内存缓存批量查询，失败不影响列表展示，组信息可为空）
            try:
                groups_map = get_user_groups_cached(server, managed_members)
                for item in managed_users:
                    groups = groups_map.get(item["username"], [])
                    priority, others = sort_user_groups(item["username"], groups)
                    item["groups_priority"] = priority
                    item["groups_other"] = others
            except Exception:  # noqa: BLE001 —— 组查询失败降级为空组展示
                for item in managed_users:
                    item["groups_priority"] = []
                    item["groups_other"] = []
        except Exception:  # noqa: BLE001 —— SSH 不可达时降级为空列表
            available_users = []
            managed_users = []
            scan_error = "读取目标机器用户失败，请稍后重试。"
    else:
        scan_error = "该服务器未关联管理凭据。"
    return {
        "server": server,
        "available_users": available_users,
        "managed_users": managed_users,
        "sys_users": User.objects.order_by("username"),
        "scan_error": scan_error,
    }


@superuser_required
def server_detail(request, pk):
    """服务器详情外壳；设备与用户状态均由浏览器并行异步加载。"""
    server = get_object_or_404(Server, pk=pk)
    return render(request, "servers/detail.html", {"server": server})


@superuser_required
@require_GET
def server_user_management(request, pk):
    """异步返回用户管理区域；缓存未命中时只进行一次 SSH 快照采集。"""
    server = get_object_or_404(Server, pk=pk)
    return render(request, "servers/_user_management.html", _server_user_context(server))


@superuser_required
@require_POST
def server_sync_users(request, pk):
    """刷新目标机器状态（仅超级管理员）：清空受管用户与设备信息缓存并重新扫描。"""
    server = get_object_or_404(Server, pk=pk)
    if not server.credential:
        messages.error(request, "该服务器未关联凭据，无法同步。")
        return redirect("servers:detail", pk=pk)
    # 刷新是只读动作：直接强制采集快照，不额外连接目标机创建用户组。
    clear_managed_users_cache(server)
    clear_device_info_cache(server)
    members, scan_msg = get_managed_users_cached(server, force_refresh=True)
    messages.success(request, f"已刷新：扫描到 {len(members)} 个受管用户（{scan_msg}）")
    return redirect("servers:detail", pk=pk)


@superuser_required
@require_POST
def server_takeover_user(request, pk):
    """将指定用户加入目标机器 nrm_managed 组（接管，仅超级管理员）。

    可选绑定一个平台用户（机器受管用户 ↔ 系统用户归属，写入 MachineUserBinding）。
    """
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    if not username:
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
        clear_managed_users_cache(server)
        clear_user_groups_cache(server)
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
@require_POST
def server_toggle_user_lock(request, pk):
    """根据目标机器真实状态切换用户启用状态（仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    if not username:
        messages.error(request, "参数错误。")
        return redirect("servers:detail", pk=pk)
    ok, msg = toggle_user_lock(server, username)
    _message_result(request, ok, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
@require_POST
def server_reset_user_password(request, pk):
    """重置受管机器用户密码，并发送给其绑定的平台用户。"""
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    if not username:
        messages.error(request, "参数错误。")
        return redirect("servers:detail", pk=pk)
    binding = (
        MachineUserBinding.objects.select_related("user")
        .filter(server=server, username=username, user__isnull=False)
        .first()
    )
    if not binding:
        messages.error(request, "该机器用户尚未绑定平台用户，无法发送重置密码。")
        return redirect("servers:detail", pk=pk)
    if not binding.user.email:
        messages.error(request, "绑定的平台用户未配置邮箱，无法发送重置密码。")
        return redirect("servers:detail", pk=pk)

    ok, password, message = reset_user_password(server, username)
    if not ok:
        messages.error(request, message)
        return redirect("servers:detail", pk=pk)
    # 旧工单中的初始密码已经失效，必须清空以免用户查看或重发错误凭据。
    Application.objects.filter(target_server=server, username=username).update(initial_password="")
    if send_machine_password_reset(binding.user, server, username, password):
        messages.success(request, f"{message}，临时密码已发送至 {binding.user.email}。")
    else:
        messages.warning(request, f"{message}，但邮件发送失败；请修复邮件配置后再次重置。")
    return redirect("servers:detail", pk=pk)


@superuser_required
@require_POST
def server_change_user_group(request, pk, action):
    """将受管用户加入或移出指定用户组（仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    group = request.POST.get("group", "").strip()
    if not username or not group:
        messages.error(request, "参数错误。")
        return redirect("servers:detail", pk=pk)
    if action == "remove" and group in {NRM_GROUP, username}:
        messages.error(request, f"{group} 是受保护的用户组，不能直接移除。")
        return redirect("servers:detail", pk=pk)
    handler = add_user_group if action == "add" else remove_user_group
    ok, msg = handler(server, username, group)
    if ok:
        clear_user_groups_cache(server)
    _message_result(request, ok, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
@require_POST
def server_update_user_groups(request, pk):
    """按目标组全集更新受管用户所属组（仅超级管理员）。

    详情页按钮灯编辑后一次性提交目标组全集（逗号分隔）；后端对比当前组
    计算加入/移出差异并批量执行，nrm_managed 标识组强制保留不可移除。
    """
    server = get_object_or_404(Server, pk=pk)
    username = request.POST.get("username", "").strip()
    if not username:
        messages.error(request, "参数错误。")
        return redirect("servers:detail", pk=pk)
    target = {g.strip() for g in request.POST.get("groups", "").split(",") if g.strip()}
    current = set(get_user_groups_cached(server, [username]).get(username, []))
    ok, msg = set_user_groups(server, username, target, current)
    _message_result(request, ok, msg)
    return redirect("servers:detail", pk=pk)


@superuser_required
@require_POST
def server_run_init(request, pk):
    """执行服务器初始化脚本（远程 get 并在目标机运行，仅超级管理员）。"""
    server = get_object_or_404(Server, pk=pk)
    ok, msg = run_init_script(server)
    _message_result(request, ok, msg)
    return redirect("servers:detail", pk=pk)
