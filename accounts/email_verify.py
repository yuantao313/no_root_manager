"""邮箱验证码服务：生成、发送、校验（含过期与重试限制）。

供两类场景使用：
- 用户修改邮箱：验证新邮箱归属（purpose=user_email，user 关联当前用户）
- SMTP 配置：写库前验证配置可用（purpose=smtp_config，user 为空，
  由发送方传入自定义发送函数来使用"待验证的配置"发信）
"""

import secrets
from datetime import timedelta

from django.utils import timezone

from notifications.services import send_email

from .models import EmailVerification

CODE_LIFETIME_MINUTES = 10
MAX_ATTEMPTS = 5


def generate_code() -> str:
    """生成 6 位数字验证码。"""
    return f"{secrets.randbelow(1000000):06d}"


def send_user_email_code(email, user) -> bool:
    """向用户邮箱发送验证码（用户修改邮箱场景）。

    返回是否发送成功；发送前会使同邮箱+同用途的旧验证码全部作废。
    """
    code = generate_code()
    EmailVerification.objects.filter(
        email=email, purpose=EmailVerification.PURPOSE_USER_EMAIL, user=user
    ).update(used=True)
    EmailVerification.objects.create(
        email=email,
        code=code,
        purpose=EmailVerification.PURPOSE_USER_EMAIL,
        user=user,
        expires_at=timezone.now() + timedelta(minutes=CODE_LIFETIME_MINUTES),
    )
    return send_email(
        "NRM 邮箱验证码",
        f"您的邮箱验证码为：{code}（{CODE_LIFETIME_MINUTES} 分钟内有效，请勿泄露）。",
        [email],
    )


def send_smtp_code(email, send_fn) -> bool:
    """向目标邮箱发送验证码（SMTP 配置验证场景）。

    send_fn(subject, body, to_list)：使用"待验证的 SMTP 配置"发送，
    从而在配置写入数据库前即可确认可用性。
    """
    code = generate_code()
    EmailVerification.objects.filter(
        email=email, purpose=EmailVerification.PURPOSE_SMTP_CONFIG, user__isnull=True
    ).update(used=True)
    EmailVerification.objects.create(
        email=email,
        code=code,
        purpose=EmailVerification.PURPOSE_SMTP_CONFIG,
        user=None,
        expires_at=timezone.now() + timedelta(minutes=CODE_LIFETIME_MINUTES),
    )
    return send_fn(
        "NRM SMTP 配置验证",
        f"您的验证码为：{code}（{CODE_LIFETIME_MINUTES} 分钟内有效）。"
        "收到本邮件说明当前 SMTP 配置可以正常发信。",
        [email],
    )


def verify_code(email, code, purpose, user=None) -> tuple[bool, str]:
    """校验验证码。

    规则：验证码须存在、未使用、未过期、匹配，且尝试次数未超限。
    返回 (是否通过, 错误信息)。
    """
    code = (code or "").strip()
    records = EmailVerification.objects.filter(
        email=email, purpose=purpose, user=user
    ).order_by("-created_at")
    if not records.exists():
        return False, "请先获取验证码。"
    rec = records.first()
    if rec.used:
        return False, "该验证码已使用，请重新获取。"
    if rec.attempts >= MAX_ATTEMPTS:
        return False, "验证码尝试次数过多，请重新获取。"
    if timezone.now() > rec.expires_at:
        rec.used = True
        rec.save(update_fields=["used"])
        return False, "验证码已过期，请重新获取。"
    if rec.code != code:
        rec.attempts += 1
        rec.save(update_fields=["attempts"])
        return False, "验证码错误，请重新输入。"
    # 校验通过：标记已使用
    rec.used = True
    rec.save(update_fields=["used"])
    return True, ""
