from django.urls import path

from . import views

app_name = "servers"

urlpatterns = [
    path("", views.server_list, name="list"),
    path("new/", views.server_create, name="create"),
    path("<int:pk>/edit/", views.server_edit, name="edit"),
    path("<int:pk>/test/", views.server_test, name="test"),
    path("<int:pk>/sync-users/", views.server_sync_users, name="sync_users"),
    path("<int:pk>/takeover/", views.server_takeover_user, name="takeover"),
    path("<int:pk>/", views.server_detail, name="detail"),
]
