"""通用装饰器：浓缩重复的管理员权限判断。

替代每个视图重复书写 @login_required + @user_passes_test(is_staff)。
"""

from django.contrib.auth.decorators import login_required, user_passes_test


def staff_required(view_func):
    """要求登录且为管理员（is_staff）的视图装饰器。"""
    return login_required(user_passes_test(lambda u: u.is_staff)(view_func))


def superuser_required(view_func):
    """要求登录且为超级管理员（is_superuser）的视图装饰器。"""
    return login_required(user_passes_test(lambda u: u.is_superuser)(view_func))
