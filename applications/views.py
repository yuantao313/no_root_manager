from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from accounts.models import Announcement
from config.decorators import staff_required
from notifications.services import (
    notify_application,
    run_in_background,
    send_provision_credentials,
)
from servers.management import (
    grant_sudo,
    provision_user,
    take_over_user,
    usermod_add_group,
    write_server_motd,
)
from servers.models import MachineUserBinding, Server

from .forms import ApplicationForm, ApplicationReviewForm
from .models import Application


def _bg_notify(application_pk, reviewed=False):
    """后台发送新申请或审批结果通知，按 pk 重新取数。"""
    application = Application.objects.select_related("target_server").get(pk=application_pk)
    notify_application(application, reviewed)


@transaction.atomic
def _record_provision(application, ok, note, password=""):
    """统一记录开通结果；成功时建立机器用户归属绑定。"""
    application.provision_note = note
    update_fields = ["provision_note", "updated_at"]
    if ok:
        application.provisioned_at = timezone.now()
        update_fields.append("provisioned_at")
        if password:
            application.initial_password = password
            update_fields.append("initial_password")
        MachineUserBinding.objects.update_or_create(
            server=application.target_server,
            username=application.username,
            defaults={"user": application.applicant, "source": application.apply_type},
        )
    application.save(update_fields=update_fields)


def _provision_existing_user(application, server):
    """执行不创建账号的申请，返回结果；未知类型返回 None。"""
    if application.apply_type == Application.ApplyType.TRANSFER:
        ok, message = take_over_user(server, application.username)
        return ok, f"已接管：{message}" if ok else f"接管失败：{message}"

    if application.apply_type == Application.ApplyType.ADMIN:
        ok, group, message = grant_sudo(server, application.username)
        return ok, f"已授予 sudo（{group}）：{message}" if ok else f"授予 sudo 失败：{message}"

    if application.apply_type != Application.ApplyType.GROUP:
        return None
    groups = application.requested_user_groups()
    if not groups:
        return False, "未选择用户组"
    errors = []
    for group in groups:
        ok, message = usermod_add_group(server, application.username, group)
        if not ok:
            errors.append(f"{group}:{message}")
    if errors:
        return False, f"加入用户组失败：{'；'.join(errors)}"
    return True, f"已加入用户组：{','.join(groups)}"


def _bg_provision(application_pk):
    """后台任务：审批通过后开通账号（SSH 操作耗时，不阻塞审批请求）。

    按 pk 重新取数（后台线程内使用独立 DB 连接）；结果写入工单字段，
    管理员/申请人稍后刷新详情页可见。granted_by 取审批人（application.reviewer）。
    """
    application = Application.objects.select_related(
        "applicant", "reviewer", "target_server", "target_server__credential"
    ).get(pk=application_pk)
    if application.status != Application.Status.APPROVED or application.provisioned_at:
        return
    if not application.target_server or not application.target_server.credential:
        _record_provision(application, False, "未开通：未关联目标服务器或凭据")
        return

    server = application.target_server
    if application.requires_superuser_approval and not (application.reviewer and application.reviewer.is_superuser):
        _record_provision(application, False, "开通失败：该申请包含 root 级权限，但审批人不是超级管理员。")
        return

    if application.apply_type != Application.ApplyType.CREATE:
        result = _provision_existing_user(application, server)
        if result is None:
            result = False, f"不支持的申请类型：{application.apply_type}"
        _record_provision(application, *result)
        return

    # 创建类型：开通新账号
    groups = list(server.default_groups_list())
    # 开通后写入目标机 motd 公告（SSH 登录显示）
    write_server_motd(server)

    ok, password, msg = provision_user(
        server,
        application.username,
        groups=groups,
        with_home=True,
    )
    _record_provision(application, ok, msg, password)
    if ok:
        # 邮件（开启时）与工单同步通知：密码 + 首次必须改密提示
        send_provision_credentials(application, password)


def _get_visible_application(user, pk):
    """按角色收窄工单查询后取详情，供查看与重发邮件复用。"""
    qs = Application.objects.with_context().visible_to(user)
    return get_object_or_404(qs, pk=pk)


def _filter_applications(request, applications, *, include_server=False):
    """统一处理工单列表的状态、类型及可选服务器筛选。"""
    filters = {
        "f_status": request.GET.get("status", "").strip(),
        "f_type": request.GET.get("apply_type", "").strip(),
    }
    if filters["f_status"] in Application.Status.values:
        applications = applications.filter(status=filters["f_status"])
    if filters["f_type"] in Application.ApplyType.values:
        applications = applications.filter(apply_type=filters["f_type"])
    if include_server:
        filters["f_server"] = request.GET.get("server", "").strip()
        if filters["f_server"].isdigit():
            applications = applications.filter(target_server_id=filters["f_server"])
    return applications, filters


@login_required
@require_POST
def application_withdraw(request, pk):
    """申请人撤回自己的待审批申请（状态 → 已撤回）。"""
    application = get_object_or_404(Application, pk=pk, applicant=request.user)
    updated = Application.objects.filter(pk=application.pk, status=Application.Status.PENDING).update(
        status=Application.Status.WITHDRAWN,
        updated_at=timezone.now(),
    )
    if not updated:
        messages.error(request, "仅待审批的申请可以撤回。")
        return redirect("applications:my")
    messages.success(request, "申请已撤回。")
    return redirect("applications:my")


@staff_required
def application_list(request):
    """申请列表（管理员）：超级管理员看全部，普通管理员仅看绑定服务器的申请。

    支持 URL query 筛选：status / apply_type / server（均可选）。
    """
    applications = Application.objects.with_context().reviewable_by(request.user)
    applications, filters = _filter_applications(request, applications, include_server=True)

    return render(
        request,
        "applications/list.html",
        {
            "applications": applications,
            **filters,
            "status_choices": Application.Status.choices,
            "type_choices": Application.ApplyType.choices,
            "servers": Server.visible_to(request.user).order_by("name"),
        },
    )


@login_required
def my_applications(request):
    """我的申请 + 新建申请（合并页）：左侧提交表单，右侧申请列表。

    任何登录用户可用；GitCode 绑定用户须先设置姓名才能提交。
    我的申请列表支持 URL query 筛选：status / apply_type（可选）。
    """
    applications = Application.objects.with_context().filter(applicant=request.user)
    applications, filters = _filter_applications(request, applications)

    # GitCode 绑定用户必须先完善个人信息（设置姓名）才能提交申请，
    # 避免以 gc<id> 占位身份进入系统
    needs_name = request.user.socialaccount_set.filter(provider="gitcode").exists() and not request.user.first_name

    form = ApplicationForm(user=request.user)
    if request.method == "POST" and not needs_name:
        form = ApplicationForm(request.POST, user=request.user)
        if form.is_valid():
            application = form.save(commit=False)
            try:
                with transaction.atomic():
                    application.save()
            except IntegrityError:
                messages.error(request, f"服务器上用户 {application.username} 已存在进行中的申请。")
                return redirect("applications:my")
            messages.success(request, "申请已提交，等待管理员审批。")
            # 通知（webhook+邮件）后台执行，不阻塞提交请求
            run_in_background(_bg_notify, application.pk)
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
            **filters,
            "status_choices": Application.Status.choices,
            "type_choices": Application.ApplyType.choices,
        },
    )


@login_required
@never_cache
def application_detail(request, pk):
    """申请详情：申请人本人可查看自己的工单；
    管理员（staff）按既有逻辑（超管全部，普通管理员仅绑定服务器的申请）。"""
    application = _get_visible_application(request.user, pk)
    has_initial_password = bool(
        application.initial_password
        and application.status == Application.Status.APPROVED
        and application.provisioned_at
    )
    is_applicant = application.applicant_id == request.user.id
    can_view_initial_password = has_initial_password and is_applicant
    can_resend_initial_password = has_initial_password and (request.user.is_superuser or is_applicant)
    can_review = Application.objects.reviewable_by(request.user).filter(pk=application.pk).exists()
    can_approve = can_review and not (application.requires_superuser_approval and not request.user.is_superuser)
    return render(
        request,
        "applications/detail.html",
        {
            "application": application,
            "can_view_initial_password": can_view_initial_password,
            "can_resend_initial_password": can_resend_initial_password,
            "can_review": can_review,
            "can_approve": can_approve,
        },
    )


@login_required
@require_POST
def application_resend_mail(request, pk):
    """重发开通凭据邮件（含初始密码）：申请人本人或超级管理员，仅限已开通工单。

    邮件正文由 send_provision_credentials 生成（含用户名/初始密码/强制改密提示），
    重发复用工单中加密存储的 initial_password，无需重新开通。
    """
    application = _get_visible_application(request.user, pk)
    if not (request.user.is_superuser or application.applicant_id == request.user.id):
        raise Http404
    if application.status != Application.Status.APPROVED or not application.provisioned_at:
        messages.error(request, "仅已开通的申请可以重发邮件。")
        return redirect("applications:detail", pk=pk)
    if not application.initial_password:
        messages.error(request, "该工单没有初始密码，无法重发凭据邮件。")
        return redirect("applications:detail", pk=pk)
    if send_provision_credentials(application, application.initial_password):
        messages.success(request, f"开通邮件已重发至 {application.email}（含初始密码）。")
    else:
        messages.error(request, "邮件发送失败：SMTP 未配置或邮箱为空，请在工单中查看初始密码。")
    return redirect("applications:detail", pk=pk)


@staff_required
@require_POST
def application_review(request, pk, action):
    """审批：action 为 approve（通过）或 reject（驳回），
    普通管理员仅能审批绑定服务器的申请。"""
    application_qs = Application.objects.with_context().reviewable_by(request.user).filter(pk=pk)
    application = get_object_or_404(application_qs)
    if action not in {"approve", "reject"}:
        messages.error(request, "无效的审批操作。")
        return redirect("applications:detail", pk=pk)

    if action == "approve" and application.requires_superuser_approval and not request.user.is_superuser:
        messages.error(request, "sudo、docker 和平台管理员属于 root 级权限，仅超级管理员可以批准。")
        return redirect("applications:detail", pk=pk)

    review_form = ApplicationReviewForm(request.POST, require_comment=action == "reject")
    if not review_form.is_valid():
        messages.error(request, review_form.errors["comment"][0])
        return redirect("applications:detail", pk=pk)

    if action == "approve":
        if not application.target_server or not application.target_server.credential:
            messages.error(request, "目标服务器未关联管理凭据，不能批准。")
            return redirect("applications:detail", pk=pk)
        if not application.target_server.ssh_host_key_fingerprint:
            messages.error(request, "目标服务器 SSH 主机指纹尚未核验，不能批准。")
            return redirect("applications:detail", pk=pk)

    new_status = Application.Status.APPROVED if action == "approve" else Application.Status.REJECTED
    reviewed_at = timezone.now()
    updated = application_qs.filter(status=Application.Status.PENDING).update(
        status=new_status,
        review_comment=review_form.cleaned_data["comment"],
        reviewer=request.user,
        reviewed_at=reviewed_at,
        updated_at=reviewed_at,
    )
    if not updated:
        messages.warning(request, "该申请已处理，不能重复审批。")
        return redirect("applications:detail", pk=pk)
    # 审批通过且指定目标服务器时：后台自动开通账号（SSH 耗时，不阻塞审批请求）。
    # 必须放在 application.save() 之后：后台任务按 pk 重新取数并回写工单字段，
    # 若在 save() 之前调度，旧实例的 save() 会把后台写入的 provisioned_at 覆盖回 None。
    if action == "approve":
        run_in_background(_bg_provision, application.pk)
        messages.success(request, "已通过申请，账号将在后台自动开通，稍后刷新详情页查看结果。")
    else:
        messages.success(request, f"已驳回申请：{application.description[:30] or application.username}")
    # 审批结果通知（webhook+邮件）后台执行，不阻塞审批请求
    run_in_background(_bg_notify, application.pk, True)
    return redirect("applications:detail", pk=pk)
