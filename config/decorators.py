"""管理员权限装饰器。"""

from django.contrib.auth.decorators import user_passes_test

staff_required = user_passes_test(lambda user: user.is_staff)
superuser_required = user_passes_test(lambda user: user.is_superuser)
