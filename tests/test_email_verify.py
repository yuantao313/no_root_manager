"""邮箱验证码签发、预检、消耗和失败限制。"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.email_verify import MAX_ATTEMPTS, send_user_email_code, verify_code
from accounts.models import EmailVerification

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="verify-user")


def _record(user, **overrides):
    values = {
        "email": "user@example.com",
        "code": "123456",
        "purpose": EmailVerification.PURPOSE_USER_EMAIL,
        "user": user,
        "expires_at": timezone.now() + timedelta(minutes=10),
    }
    values.update(overrides)
    return EmailVerification.objects.create(**values)


def test_new_code_invalidates_previous_code(user):
    with (
        patch("accounts.email_verify.generate_code", side_effect=["111111", "222222"]),
        patch("accounts.email_verify.send_email", return_value=True),
    ):
        send_user_email_code("user@example.com", user)
        send_user_email_code("user@example.com", user)

    old, current = EmailVerification.objects.order_by("created_at")
    assert (old.code, old.used) == ("111111", True)
    assert (current.code, current.used) == ("222222", False)


def test_preview_then_consume_code(user):
    record = _record(user)
    args = (record.email, record.code, record.purpose)

    assert verify_code(*args, user=user, consume=False) == (True, "")
    record.refresh_from_db()
    assert record.used is False
    assert verify_code(*args, user=user) == (True, "")
    record.refresh_from_db()
    assert record.used is True


def test_wrong_code_reaches_attempt_limit(user):
    record = _record(user)
    args = (record.email, "000000", record.purpose)

    for _ in range(MAX_ATTEMPTS):
        assert verify_code(*args, user=user)[0] is False
    assert "次数过多" in verify_code(*args, user=user)[1]


def test_expired_code_is_invalidated(user):
    record = _record(user, expires_at=timezone.now() - timedelta(seconds=1))
    ok, message = verify_code(record.email, record.code, record.purpose, user=user)

    record.refresh_from_db()
    assert ok is False and "已过期" in message
    assert record.used is True


def test_missing_code_uses_single_query(user, django_assert_num_queries):
    with django_assert_num_queries(1):
        assert verify_code("none@example.com", "123456", EmailVerification.PURPOSE_USER_EMAIL, user=user)[0] is False
