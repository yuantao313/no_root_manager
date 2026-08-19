"""工单通知与远程开通编排。"""

from django.db import transaction
from django.utils import timezone

from notifications.services import notify_application, send_provision_credentials
from servers.management import (
    clear_managed_users_cache,
    clear_user_groups_cache,
    grant_sudo,
    provision_user,
    take_over_user,
    usermod_add_group,
    write_server_motd,
)
from servers.models import MachineUserBinding

from .models import Application


def notify_application_by_pk(application_pk, reviewed=False):
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
        clear_managed_users_cache(application.target_server)
        clear_user_groups_cache(application.target_server)
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


def provision_application_by_pk(application_pk):
    """审批通过后执行远程开通，并把结果写回工单。"""
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

    groups = list(server.default_groups_list())
    write_server_motd(server)
    ok, password, message = provision_user(
        server,
        application.username,
        groups=groups,
        with_home=True,
    )
    _record_provision(application, ok, message, password)
    if ok:
        send_provision_credentials(application, password)
