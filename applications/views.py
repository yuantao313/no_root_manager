from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Announcement
from config.decorators import staff_required
from notifications.services import (
    notify_new_application,
    notify_review_result,
    run_in_background,
    send_provision_credentials,
    webhook_new_application,
    webhook_review_result,
)
from servers.management import (
    grant_npu_access,
    grant_sudo,
    provision_user,
    take_over_user,
    usermod_add_group,
    write_server_motd,
)
from servers.models import MachineUserBinding, Server

from .forms import ApplicationForm
from .models import Application, SudoGrant


def _bg_notify_new_application(application_pk):
    """后台任务：新申请通知（webhook + 邮件），按 pk 重新取数。"""
    application = Application.objects.get(pk=application_pk)
    # 先推 webhook（5 秒超时兜底），再发邮件（SMTP 不可达时最多等 10 秒）
    webhook_new_application(application)
    notify_new_application(application)


def _bg_notify_review_result(application_pk):
    """后台任务：审批结果通知（webhook + 邮件），按 pk 重新取数。"""
    application = Application.objects.get(pk=application_pk)
    notify_review_result(application)
    webhook_review_result(application)


def _bg_provision(application_pk):
    """后台任务：审批通过后开通账号（SSH 操作耗时，不阻塞审批请求）。

    按 pk 重新取数（后台线程内使用独立 DB 连接）；结果写入工单字段，
    管理员/申请人稍后刷新详情页可见。granted_by 取审批人（application.reviewer）。
    """
    application = Application.objects.select_related("target_server", "target_server__credential").get(
        pk=application_pk
    )
    if not application.target_server or not application.target_server.credential:
        application.provision_note = "未开通：未关联目标服务器或凭据"
        application.save(update_fields=["provision_note"])
        return

    server = application.target_server

    # 转移类型：将目标机器已有用户接管为受管用户（用户信息实时扫描，不落库）
    if application.apply_type == Application.ApplyType.TRANSFER:
        ok, msg = take_over_user(server, application.username)
        application.provision_note = f"已接管：{msg}" if ok else f"接管失败：{msg}"
        if ok:
            application.provisioned_at = timezone.now()
            # 归属绑定：机器用户 → 申请人（唯一约束防重复接管）
            MachineUserBinding.objects.update_or_create(
                server=server,
                username=application.username,
                defaults={"user": application.applicant, "source": "transfer"},
            )
        application.save()
        return

    # 平台管理员类型：不开新账号，直接授予所选服务器的 sudo 权限（username=登录用户名）
    if application.apply_type == Application.ApplyType.ADMIN:
        ok, group, msg = grant_sudo(server, application.username)
        application.provision_note = f"已授予 sudo（{group}）：{msg}" if ok else f"授予 sudo 失败：{msg}"
        if ok:
            application.provisioned_at = timezone.now()
            MachineUserBinding.objects.update_or_create(
                server=server,
                username=application.username,
                defaults={"user": application.applicant, "source": "admin"},
            )
        application.save()
        return

    # 申请用户组类型：不建号不转移，把登录用户加入所选用户组（usermod -aG）
    if application.apply_type == Application.ApplyType.GROUP:
        group_list = [g.strip() for g in (application.user_groups or "").split(",") if g.strip()]
        if not group_list:
            application.provision_note = "未选择用户组"
            application.save(update_fields=["provision_note"])
            return
        ok = True
        errors = []
        for g in group_list:
            g_ok, g_msg = usermod_add_group(server, application.username, g)
            if not g_ok:
                ok = False
                errors.append(f"{g}:{g_msg}")
        application.provision_note = (
            f"已加入用户组：{','.join(group_list)}"
            if ok
            else f"加入用户组失败：{'；'.join(errors)}"
        )
        if ok:
            application.provisioned_at = timezone.now()
            MachineUserBinding.objects.update_or_create(
                server=server,
                username=application.username,
                defaults={"user": application.applicant, "source": "group"},
            )
        application.save()
        return

    # 创建类型：开通新账号
    groups = list(server.default_groups_list())
    applied = [g.strip() for g in (application.applied_groups or "").split(",") if g.strip()]
    for g in applied:
        if g not in groups:
            groups.append(g)

    # 开通后写入目标机 motd 公告（SSH 登录显示）
    _ok_notice, _msg_notice = write_server_motd(server)

    # 使用截止时间：到期后机器账号自动失效（usermod -e）
    expire_date = application.valid_until.date() if application.valid_until else None

    ok, password, msg = provision_user(
        server,
        application.username,
        groups=groups,
        expire_date=expire_date,
        with_home=True,
    )
    application.provision_note = msg
    if ok:
        application.provisioned_at = timezone.now()
        # 初始密码同步写入工单（加密存储）：邮件不可用时管理员/申请人可从工单获取
        application.initial_password = password
        # 大一统归属绑定：创建类型开通的机器用户也入表（机器用户 → 申请人）
        MachineUserBinding.objects.update_or_create(
            server=server,
            username=application.username,
            defaults={"user": application.applicant, "source": "create"},
        )

        # NPU 服务器：分组选择即 NPU 卡组，用户开通后执行卡授权（usermod -aG npu,npuN）。
        # 必须在 provision_user 之后：用户未创建时 usermod 会报"用户不存在"
        if server.is_npu and applied:
            # 自动附带 npu 公共组（前端提交时已附带，此处兜底防绕过）
            npu_groups = applied if "npu" in applied else ["npu"] + applied
            grant_npu_access(server, application.username, npu_groups)

        # 申请了 sudo 权限：授予并记录审计日志（当天有效）
        if application.needs_sudo:
            _grant_sudo_for_application(application)

        # 邮件（开启时）与工单同步通知：密码 + 首次必须改密提示
        send_provision_credentials(application, password, expire_date=expire_date)
    application.save()


def _grant_sudo_for_application(application):
    """授予 sudo 权限并记录 SudoGrant 审计日志（当日 23:59:59 失效）。

    后台任务内调用（无 request）：granted_by 取审批人 application.reviewer。
    """
    server = application.target_server
    ok, group, msg = grant_sudo(server, application.username)
    # 当日 23:59:59 失效（次日需重新申请）
    expires_at = timezone.localtime().replace(hour=23, minute=59, second=59, microsecond=0)
    SudoGrant.objects.create(
        application=application,
        server=server,
        username=application.username,
        granted_by=application.reviewer,
        expires_at=expires_at,
        status=SudoGrant.Status.ACTIVE if ok else SudoGrant.Status.EXPIRED,
        revoke_note=f"授予：{msg}" if ok else f"授予失败：{msg}",
    )
    # 结果写入独立的 sudo_note 字段，避免与开通信息（provision_note）混淆
    application.sudo_note = msg
    application.save(update_fields=["sudo_note"])


@login_required
def application_withdraw(request, pk):
    """申请人撤回自己的待审批申请（状态 → 已撤回）。"""
    application = get_object_or_404(Application, pk=pk, applicant=request.user)
    if request.method != "POST":
        return redirect("applications:my")
    if application.status != Application.Status.PENDING:
        messages.error(request, "仅待审批的申请可以撤回。")
        return redirect("applications:my")
    application.status = Application.Status.WITHDRAWN
    application.save(update_fields=["status"])
    messages.success(request, "申请已撤回。")
    return redirect("applications:my")


@staff_required
def application_list(request):
    """申请列表（管理员）：超级管理员看全部，普通管理员仅看绑定服务器的申请。

    支持 URL query 筛选：status / apply_type / server（均可选）。
    """
    qs = Application.objects.select_related("applicant", "target_server", "reviewer")
    if request.user.is_superuser:
        applications = qs.all()
    else:
        applications = qs.filter(target_server__in=Server.visible_to(request.user))

    # 筛选参数
    f_status = request.GET.get("status", "").strip()
    f_type = request.GET.get("apply_type", "").strip()
    f_server = request.GET.get("server", "").strip()
    if f_status in Application.Status.values:
        applications = applications.filter(status=f_status)
    if f_type in Application.ApplyType.values:
        applications = applications.filter(apply_type=f_type)
    if f_server.isdigit():
        applications = applications.filter(target_server_id=f_server)

    return render(
        request,
        "applications/list.html",
        {
            "applications": applications,
            "f_status": f_status,
            "f_type": f_type,
            "f_server": f_server,
            "status_choices": Application.Status.choices,
            "type_choices": Application.ApplyType.choices,
            "servers": Server.objects.all().order_by("name"),
        },
    )


@login_required
def my_applications(request):
    """我的申请 + 新建申请（合并页）：左侧提交表单，右侧申请列表。

    任何登录用户可用；GitCode 绑定用户须先设置姓名才能提交。
    我的申请列表支持 URL query 筛选：status / apply_type（可选）。
    """
    applications = Application.objects.select_related("applicant", "target_server", "reviewer").filter(
        applicant=request.user
    )

    # 我的申请筛选参数（状态/类型）
    f_status = request.GET.get("status", "").strip()
    f_type = request.GET.get("apply_type", "").strip()
    if f_status in Application.Status.values:
        applications = applications.filter(status=f_status)
    if f_type in Application.ApplyType.values:
        applications = applications.filter(apply_type=f_type)

    # GitCode 绑定用户必须先完善个人信息（设置姓名）才能提交申请，
    # 避免以 gc<id> 占位身份进入系统
    needs_name = request.user.socialaccount_set.filter(provider="gitcode").exists() and not request.user.first_name

    form = ApplicationForm()
    if request.method == "POST" and not needs_name:
        form = ApplicationForm(request.POST)
        if form.is_valid():
            application = form.save(commit=False)
            application.applicant = request.user
            # 身份信息/工号/目标用户名全部来自账号，不再由申请表单填写
            application.applicant_name = request.user.first_name or request.user.username
            application.email = request.user.email
            application.employee_id = getattr(getattr(request.user, "profile", None), "employee_id", "") or ""
            # 申请的用户组（创建类型）：逗号分隔存储
            user_groups = form.cleaned_data.get("user_groups") or []
            application.user_groups = ",".join(user_groups)

            if application.apply_type == Application.ApplyType.TRANSFER:
                # 转移类型：目标机器已有用户名（从前端机器用户下拉选择）
                application.username = (form.cleaned_data.get("transfer_username") or "").strip()
                if not application.username:
                    messages.error(request, "请从机器用户列表中选择要接管的账号。")
                    return redirect("applications:my")
            else:
                # 创建/管理员类型：用户名直接用登录用户名
                application.username = request.user.username

            # 防重复申请：同一服务器 + 用户名 已有进行中的申请（待审批/已通过）则禁止提交
            dup = Application.objects.filter(
                target_server=application.target_server,
                username=application.username,
                status__in=[Application.Status.PENDING, Application.Status.APPROVED],
            ).exclude(pk=application.pk)
            if dup.exists():
                messages.error(
                    request,
                    f"服务器上用户 {application.username} 已存在进行中的申请，请勿重复申请。",
                )
                return redirect("applications:my")

            application.save()
            messages.success(request, "申请已提交，等待管理员审批。")
            # 通知（webhook+邮件）后台执行，不阻塞提交请求
            run_in_background(_bg_notify_new_application, application.pk)
            return redirect("applications:my")

    return render(
        request,
        "applications/my_list.html",
        {
            "applications": applications,
            "form": form,
            "needs_name": needs_name,
            # 系统首页同步显示启用中的公告
            "announcements": Announcement.objects.filter(enabled=True),
            # 我的申请筛选上下文
            "f_status": f_status,
            "f_type": f_type,
            "status_choices": Application.Status.choices,
            "type_choices": Application.ApplyType.choices,
        },
    )


@login_required
def application_detail(request, pk):
    """申请详情：申请人本人可查看自己的工单；
    管理员（staff）按既有逻辑（超管全部，普通管理员仅绑定服务器的申请）。"""
    qs = Application.objects.select_related("applicant", "target_server", "reviewer")
    if request.user.is_superuser:
        application = get_object_or_404(qs, pk=pk)
    elif request.user.is_staff:
        application = get_object_or_404(
            qs.filter(target_server__in=Server.visible_to(request.user)), pk=pk
        )
    else:
        # 普通用户：只能查看自己的工单（他人工单 404，防止越权）
        application = get_object_or_404(qs, pk=pk, applicant=request.user)
    return render(request, "applications/detail.html", {"application": application})


@staff_required
def application_review(request, pk, action):
    """审批：action 为 approve（通过）或 reject（驳回），
    普通管理员仅能审批绑定服务器的申请。"""
    qs = Application.objects.select_related("applicant", "target_server", "reviewer")
    if request.user.is_superuser:
        application = get_object_or_404(qs, pk=pk)
    else:
        application = get_object_or_404(
            qs.filter(target_server__in=Server.visible_to(request.user)), pk=pk
        )
    if application.status != Application.Status.PENDING:
        messages.warning(request, "该申请已处理，不能重复审批。")
        return redirect("applications:detail", pk=pk)

    comment = request.POST.get("comment", "").strip()
    if action == "approve":
        application.status = Application.Status.APPROVED
        application.review_comment = comment
        messages.success(request, f"已通过申请：{application.description[:30] or application.username}")
    elif action == "reject":
        application.status = Application.Status.REJECTED
        application.review_comment = comment
        messages.success(request, f"已驳回申请：{application.description[:30] or application.username}")
    else:
        messages.error(request, "无效的审批操作。")
        return redirect("applications:detail", pk=pk)

    application.reviewer = request.user
    application.reviewed_at = timezone.now()
    application.save()
    # 审批通过且指定目标服务器时：后台自动开通账号（SSH 耗时，不阻塞审批请求）。
    # 必须放在 application.save() 之后：后台任务按 pk 重新取数并回写工单字段，
    # 若在 save() 之前调度，旧实例的 save() 会把后台写入的 provisioned_at 覆盖回 None。
    if action == "approve":
        run_in_background(_bg_provision, application.pk)
        messages.success(request, "已通过申请，账号将在后台自动开通，稍后刷新详情页查看结果。")
    # 审批结果通知（webhook+邮件）后台执行，不阻塞审批请求
    run_in_background(_bg_notify_review_result, application.pk)
    return redirect("applications:detail", pk=pk)
