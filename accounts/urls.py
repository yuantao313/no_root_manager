from django.urls import path
from django.contrib.auth import views as auth_views

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("profile/", views.profile, name="profile"),
    path("gitcode/login/", views.gitcode_login, name="gitcode_login"),
    path("gitcode/callback/", views.gitcode_callback, name="gitcode_callback"),
    path("api/username-suggestions/", views.username_suggestions, name="username-suggestions"),
    path("login/", views.GitCodeLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
