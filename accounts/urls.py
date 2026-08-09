from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("profile/", views.profile, name="profile"),
    path("api/send-email-code/", views.send_email_code_ajax, name="send_email_code_ajax"),
    path("api/verify-email-code/", views.verify_email_code_ajax, name="verify_email_code_ajax"),
    path("set-password/", views.set_password, name="set_password"),
    path("settings/", views.settings, name="settings"),
    path("password_reset/", views.NRMPasswordResetView.as_view(), name="password_reset"),
    path(
        "password_reset/done/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password_reset_complete",
    ),
    path("gitcode/unbind/", views.gitcode_unbind, name="gitcode_unbind"),
    path("api/username-suggestions/", views.username_suggestions, name="username-suggestions"),
    path("login/", views.GitCodeLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
