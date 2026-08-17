from django.urls import path

from . import views

app_name = "servers"

urlpatterns = [
    path("", views.server_list, name="list"),
    path("new/", views.server_create, name="create"),
    path("api/device/<int:pk>/", views.server_device_api, name="device_api"),
    path("<int:pk>/edit/", views.server_edit, name="edit"),
    path("<int:pk>/test/", views.server_test, name="test"),
    path("<int:pk>/sync-users/", views.server_sync_users, name="sync_users"),
    path("<int:pk>/takeover/", views.server_takeover_user, name="takeover"),
    path("<int:pk>/lock/", views.server_set_user_lock, {"action": "lock"}, name="lock_user"),
    path("<int:pk>/unlock/", views.server_set_user_lock, {"action": "unlock"}, name="unlock_user"),
    path("<int:pk>/user-group/add/", views.server_change_user_group, {"action": "add"}, name="add_user_group"),
    path(
        "<int:pk>/user-group/remove/",
        views.server_change_user_group,
        {"action": "remove"},
        name="remove_user_group",
    ),
    path("<int:pk>/user-group/update/", views.server_update_user_groups, name="update_user_groups"),
    path("<int:pk>/run-init/", views.server_run_init, name="run_init"),
    path("<int:pk>/", views.server_detail, name="detail"),
]
