"""邮箱验证码服务：生成、发送、校验（含过期与重试限制）。

供两类场景使用：
- 用户修改邮箱：验证新邮箱归属（purpose=user_email，user 关联当前用户）
- SMTP 配置：写库前验证配置可用（purpose=smtp_config，user 为空，
  由发送方传入自定义发送函数来使用"待验证的配置"发信）
"""

import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from notifications.services import send_email

from .models import EmailVerification

CODE_LIFETIME_MINUTES = 10
MAX_ATTEMPTS = 5


def generate_code() -> str:
    """生成 6 位数字验证码。"""
    return f"{secrets.randbelow(1000000):06d}"


@transaction.atomic
def _issue_code(email, purpose, user=None) -> tuple[str, EmailVerification]:
    """作废同场景旧验证码并签发新验证码。"""
    code = generate_code()
    EmailVerification.objects.filter(email=email, purpose=purpose, user=user).update(used=True)
    record = EmailVerification.objects.create(
        email=email,
        code=make_password(code),
        purpose=purpose,
        user=user,
        expires_at=timezone.now() + timedelta(minutes=CODE_LIFETIME_MINUTES),
    )
    return code, record


def _invalidate_if_send_failed(record, sent: bool) -> bool:
    """发送失败时立即作废无法送达的验证码，避免留下可猜测的有效记录。"""
    if not sent:
        EmailVerification.objects.filter(pk=record.pk).update(used=True)
    return sent


def send_user_email_code(email, user) -> bool:
    """向用户邮箱发送验证码（用户修改邮箱场景）。

    返回是否发送成功；发送前会使同邮箱+同用途的旧验证码全部作废。
    """
    code, record = _issue_code(email, EmailVerification.PURPOSE_USER_EMAIL, user)
    sent = send_email(
        "NRM 邮箱验证码",
        f"您的邮箱验证码为：{code}（{CODE_LIFETIME_MINUTES} 分钟内有效，请勿泄露）。",
        [email],
    )
    return _invalidate_if_send_failed(record, sent)


def send_smtp_code(email, send_fn) -> bool:
    """向目标邮箱发送验证码（SMTP 配置验证场景）。

    send_fn(subject, body, to_list)：使用"待验证的 SMTP 配置"发送，
    从而在配置写入数据库前即可确认可用性。
    """
    code, record = _issue_code(email, EmailVerification.PURPOSE_SMTP_CONFIG)
    sent = send_fn(
        "NRM SMTP 配置验证",
        f"您的验证码为：{code}（{CODE_LIFETIME_MINUTES} 分钟内有效）。收到本邮件说明当前 SMTP 配置可以正常发信。",
        [email],
    )
    return _invalidate_if_send_failed(record, sent)


def verify_code(email, code, purpose, user=None, consume=True) -> tuple[bool, str]:
    """校验验证码。

    规则：验证码须存在、未使用、未过期、匹配，且尝试次数未超限。
    consume=True：校验通过后标记已使用（真正的消耗，保存邮箱/配置时用）；
    consume=False：仅校验不消耗（前端 AJAX 预检用，避免被二次校验拦截）。
    返回 (是否通过, 错误信息)。
    """
    code = (code or "").strip()
    records = EmailVerification.objects.filter(email=email, purpose=purpose, user=user).order_by("-created_at")
    rec = records.first()
    if rec is None:
        return False, "请先获取验证码。"
    if rec.used:
        return False, "该验证码已使用，请重新获取。"
    if rec.attempts >= MAX_ATTEMPTS:
        return False, "验证码尝试次数过多，请重新获取。"
    if timezone.now() > rec.expires_at:
        rec.used = True
        rec.save(update_fields=["used"])
        return False, "验证码已过期，请重新获取。"
    if not check_password(code, rec.code):
        EmailVerification.objects.filter(pk=rec.pk, attempts__lt=MAX_ATTEMPTS).update(attempts=F("attempts") + 1)
        return False, "验证码错误，请重新输入。"
    # 校验通过：仅 consume=True 时标记已使用
    if consume:
        consumed = EmailVerification.objects.filter(pk=rec.pk, used=False).update(used=True)
        if not consumed:
            return False, "该验证码已使用，请重新获取。"
    return True, ""
