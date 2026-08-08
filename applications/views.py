from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from config.decorators import staff_required
from notifications.services import (
    notify_new_application,
    notify_review_result,
    send_provision_credentials,
    webhook_new_application,
    webhook_review_result,
)
from servers.management import grant_sudo, migrate_home_dir, provision_user
from servers.models import Server

from .forms import ApplicationForm
from .models import Application, SudoGrant


def _provision_on_approve(application, request):
    """申请通过时在目标机器开通账号：建用户、随机密码并邮件发送。"""
    if not application.target_server or not application.target_server.credential:
        application.provision_note = "未开通：未关联目标服务器或凭据"
        application.save()
        return

    server = application.target_server
    # 分组：服务器默认组 + 用户申请时勾选的附加组（均为字符串逗号分隔）
    groups = list(server.default_groups_list())
    applied = [g.strip() for g in (application.applied_groups or "").split(",") if g.strip()]
    for g in applied:
        if g not in groups:
            groups.append(g)

    # 使用截止时间：到期后机器账号自动失效（usermod -e）
    expire_date = application.valid_until.date() if application.valid_until else None

    # 申请了目录迁移时不预建 home，让迁移流程把用户已有目录移过来
    with_home = not bool(application.migrate_from_dir)

    ok, password, msg = provision_user(
        server,
        application.username,
        groups=groups,
        expire_date=expire_date,
        with_home=with_home,
    )
    application.provision_note = msg
    if ok:
        application.provisioned_at = timezone.now()
        application.save()
        messages.success(request, f"账号已开通：{msg}")
        send_provision_credentials(application, password, expire_date=expire_date)
        # 申请了目录迁移：将用户已有目录迁移到 /home/username
        if application.migrate_from_dir:
            mok, mmsg = migrate_home_dir(server, application.migrate_from_dir, application.username)
            application.provision_note = (
                (application.provision_note + "\n" + mmsg) if application.provision_note else mmsg
            )
            application.save(update_fields=["provision_note"])
            if mok:
                messages.success(request, f"目录迁移成功：{mmsg}")
            else:
                messages.warning(request, f"目录迁移失败：{mmsg}")
        # 申请了 sudo 权限：授予并记录审计日志（当天有效）
        if application.needs_sudo:
            _grant_sudo_for_application(application, request)
    else:
        application.save()
        messages.warning(request, f"开通失败：{msg}")


def _grant_sudo_for_application(application, request):
    """授予 sudo 权限并记录 SudoGrant 审计日志（当日 23:59:59 失效）。"""
    server = application.target_server
    ok, group, msg = grant_sudo(server, application.username)
    # 当日 23:59:59 失效（次日需重新申请）
    expires_at = timezone.localtime().replace(hour=23, minute=59, second=59, microsecond=0)
    SudoGrant.objects.create(
        application=application,
        server=server,
        username=application.username,
        granted_by=request.user,
        expires_at=expires_at,
        status=SudoGrant.Status.ACTIVE if ok else SudoGrant.Status.EXPIRED,
        revoke_note=f"授予：{msg}" if ok else f"授予失败：{msg}",
    )
    # 结果写入独立的 sudo_note 字段，避免与开通信息（provision_note）混淆
    application.sudo_note = msg
    application.save(update_fields=["sudo_note"])
    if ok:
        messages.success(request, f"已授予 sudo 权限（{group}，当天有效）：{msg}")
    else:
        messages.warning(request, f"sudo 权限授予失败：{msg}")


@staff_required
def application_list(request):
    """申请列表（管理员）：超级管理员看全部，普通管理员仅看绑定服务器的申请。"""
    if request.user.is_superuser:
        applications = Application.objects.all()
    else:
        applications = Application.objects.filter(target_server__in=Server.visible_to(request.user))
    return render(request, "applications/list.html", {"applications": applications})


@login_required
def my_applications(request):
    """我的申请 + 新建申请（合并页）：左侧提交表单，右侧申请列表。

    任何登录用户可用；GitCode 绑定用户须先设置姓名才能提交。
    """
    applications = Application.objects.filter(applicant=request.user)

    # GitCode 绑定用户必须先完善个人信息（设置姓名）才能提交申请，
    # 避免以 gc<id> 占位身份进入系统
    needs_name = request.user.socialaccount_set.filter(provider="gitcode").exists() and not request.user.first_name

    form = ApplicationForm()
    if request.method == "POST" and not needs_name:
        form = ApplicationForm(request.POST)
        if form.is_valid():
            application = form.save(commit=False)
            application.applicant = request.user
            # 身份信息来自账号（个人中心维护），不再由申请表单填写
            application.applicant_name = request.user.first_name or request.user.username
            application.email = request.user.email
            application.save()
            messages.success(request, "申请已提交，等待管理员审批。")
            notify_new_application(application)
            webhook_new_application(application)
            return redirect("applications:my")

    return render(
        request,
        "applications/my_list.html",
        {
            "applications": applications,
            "form": form,
            "needs_name": needs_name,
        },
    )


@staff_required
def application_detail(request, pk):
    """申请详情（管理员，普通管理员仅限绑定服务器的申请）。"""
    if request.user.is_superuser:
        qs = Application.objects.all()
    else:
        qs = Application.objects.filter(target_server__in=Server.visible_to(request.user))
    application = get_object_or_404(qs, pk=pk)
    return render(request, "applications/detail.html", {"application": application})


@staff_required
def application_review(request, pk, action):
    """审批：action 为 approve（通过）或 reject（驳回），
    普通管理员仅能审批绑定服务器的申请。"""
    if request.user.is_superuser:
        qs = Application.objects.all()
    else:
        qs = Application.objects.filter(target_server__in=Server.visible_to(request.user))
    application = get_object_or_404(qs, pk=pk)
    if application.status != Application.Status.PENDING:
        messages.warning(request, "该申请已处理，不能重复审批。")
        return redirect("applications:detail", pk=pk)

    comment = request.POST.get("comment", "").strip()
    if action == "approve":
        application.status = Application.Status.APPROVED
        application.review_comment = comment
        messages.success(request, f"已通过申请：{application.title}")
        # 申请通过且指定了目标服务器时，自动在机器上开通账号
        _provision_on_approve(application, request)
    elif action == "reject":
        application.status = Application.Status.REJECTED
        application.review_comment = comment
        messages.success(request, f"已驳回申请：{application.title}")
    else:
        messages.error(request, "无效的审批操作。")
        return redirect("applications:detail", pk=pk)

    application.reviewer = request.user
    application.reviewed_at = timezone.now()
    application.save()
    notify_review_result(application)
    webhook_review_result(application)
    return redirect("applications:detail", pk=pk)
