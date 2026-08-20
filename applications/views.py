import logging

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
from config.pagination import paginate
from notifications.services import run_in_background, send_provision_credentials
from servers.models import Server

from .forms import ApplicationForm, ApplicationReviewForm
from .models import Application
from .services import notify_application_by_pk, provision_application_by_pk

logger = logging.getLogger(__name__)


def _get_visible_application(user, pk):
    """按角色收窄工单查询后取详情，供查看与重发邮件复用。"""
    qs = Application.objects.with_context().visible_to(user)
    return get_object_or_404(qs, pk=pk)


def _filter_applications(request, applications, *, include_server=False, default_status=""):
    """统一处理工单列表的状态、类型及可选服务器筛选。"""
    requested_status = request.GET.get("status")
    filters = {
        "f_status": default_status if requested_status is None else requested_status.strip(),
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


def _provision_and_report(request, application, *, retried=False):
    """可靠执行机器开通并把稳定结果反馈给操作者。"""
    try:
        provision_application_by_pk(application.pk)
    except Exception:  # noqa: BLE001 —— 异常必须落库并给用户稳定反馈
        logger.exception("工单开通异常：application=%s", application.pk)
        Application.objects.filter(pk=application.pk, provisioned_at__isnull=True).update(
            provision_note="开通异常：请检查服务日志后重试或人工处理。",
            updated_at=timezone.now(),
        )
    application.refresh_from_db(fields=["provisioned_at", "provision_note"])
    if application.provisioned_at:
        prefix = "重试成功" if retried else "申请已通过"
        messages.success(request, f"{prefix}，目标机器操作已完成。")
    else:
        messages.error(request, f"机器操作未完成：{application.provision_note or '未知错误'}")


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
    applications, filters = _filter_applications(
        request,
        applications,
        include_server=True,
        default_status=Application.Status.PENDING,
    )
    page_obj, page_query = paginate(request, applications)

    return render(
        request,
        "applications/list.html",
        {
            "applications": page_obj.object_list,
            "page_obj": page_obj,
            "page_query": page_query,
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
    page_obj, page_query = paginate(request, applications)

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
            run_in_background(notify_application_by_pk, application.pk)
            return redirect("applications:my")

    return render(
        request,
        "applications/my_list.html",
        {
            "applications": page_obj.object_list,
            "page_obj": page_obj,
            "page_query": page_query,
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
    can_retry_provision = (
        can_approve and application.status == Application.Status.APPROVED and not application.provisioned_at
    )
    return render(
        request,
        "applications/detail.html",
        {
            "application": application,
            "can_view_initial_password": can_view_initial_password,
            "can_resend_initial_password": can_resend_initial_password,
            "can_review": can_review,
            "can_approve": can_approve,
            "can_retry_provision": can_retry_provision,
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
    # SSH 开通属于关键控制操作：当前项目没有持久任务队列，因此同步执行，
    # 避免 Web 进程退出时 daemon 线程静默丢任务。通知仍可后台发送。
    if action == "approve":
        _provision_and_report(request, application)
    else:
        messages.success(request, f"已驳回申请：{application.description[:30] or application.username}")
    # 审批结果通知（webhook+邮件）后台执行，不阻塞审批请求
    run_in_background(notify_application_by_pk, application.pk, True)
    return redirect("applications:detail", pk=pk)


@staff_required
@require_POST
def application_retry_provision(request, pk):
    """重试已批准但尚未完成的机器操作，权限范围与审批保持一致。"""
    application = get_object_or_404(Application.objects.with_context().reviewable_by(request.user), pk=pk)
    if application.status != Application.Status.APPROVED or application.provisioned_at:
        messages.error(request, "仅能重试已通过但尚未完成的机器操作。")
        return redirect("applications:detail", pk=pk)
    if application.requires_superuser_approval and not request.user.is_superuser:
        raise Http404
    if not application.target_server or not application.target_server.credential:
        messages.error(request, "目标服务器未关联管理凭据，不能重试。")
        return redirect("applications:detail", pk=pk)
    if not application.target_server.ssh_host_key_fingerprint:
        messages.error(request, "目标服务器 SSH 主机指纹尚未核验，不能重试。")
        return redirect("applications:detail", pk=pk)
    _provision_and_report(request, application, retried=True)
    return redirect("applications:detail", pk=pk)
