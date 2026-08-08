"""项目根路由视图。"""

from django.shortcuts import redirect


def index(request):
    """根路径 /：按登录状态跳转到合适入口。"""
    if request.user.is_authenticated:
        return redirect("applications:my")
    return redirect("accounts:login")
