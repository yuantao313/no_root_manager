from django.urls import path
from django.contrib.auth import views as auth_views

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("profile/", views.profile, name="profile"),
    path("settings/", views.settings, name="settings"),
    path("gitcode/login/", views.gitcode_login, name="gitcode_login"),
    path("gitcode/callback/", views.gitcode_callback, name="gitcode_callback"),
    path("gitcode/bind/", views.gitcode_bind, name="gitcode_bind"),
    path("gitcode/unbind/", views.gitcode_unbind, name="gitcode_unbind"),
    path("api/username-suggestions/", views.username_suggestions, name="username-suggestions"),
    path("login/", auth_views.LoginView.as_view(
        template_name="accounts/login.html",
        redirect_authenticated_user=True,
    ), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
