"""管理员权限装饰器：匿名用户登录，已登录但越权时明确返回 403。"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def _role_required(predicate):
    """保留 Django 登录跳转语义，同时避免把已登录的越权用户伪装成未登录。"""

    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not predicate(request.user):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


staff_required = _role_required(lambda user: user.is_active and user.is_staff)
superuser_required = _role_required(lambda user: user.is_active and user.is_superuser)
