"""NRM 根路由。"""

from django.contrib import admin
from django.urls import include, path

from accounts.views import social_signup

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    # 覆盖 allauth 的社交注册页（Tab：创建新账号 / 绑定已有账号）
    path("accounts/allauth/3rdparty/signup/", social_signup, name="socialaccount_signup"),
    path("accounts/allauth/", include("allauth.urls")),
    path("select2/", include("django_select2.urls")),
    path("applications/", include("applications.urls")),
    path("servers/", include("servers.urls")),
    path("notifications/", include("notifications.urls")),
]
