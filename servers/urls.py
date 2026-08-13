from django.urls import path

from . import views

app_name = "servers"

urlpatterns = [
    path("", views.server_list, name="list"),
    path("new/", views.server_create, name="create"),
    path("api/groups/<int:pk>/", views.server_groups_api, name="groups_api"),
    path("api/device/<int:pk>/", views.server_device_api, name="device_api"),
    path("<int:pk>/edit/", views.server_edit, name="edit"),
    path("<int:pk>/test/", views.server_test, name="test"),
    path("<int:pk>/sync-users/", views.server_sync_users, name="sync_users"),
    path("<int:pk>/takeover/", views.server_takeover_user, name="takeover"),
    path("<int:pk>/lock/", views.server_lock_user, name="lock_user"),
    path("<int:pk>/unlock/", views.server_unlock_user, name="unlock_user"),
    path("<int:pk>/user-group/add/", views.server_add_user_group, name="add_user_group"),
    path("<int:pk>/user-group/remove/", views.server_remove_user_group, name="remove_user_group"),
    path("<int:pk>/run-init/", views.server_run_init, name="run_init"),
    path("<int:pk>/configure-npu/", views.server_configure_npu, name="configure_npu"),
    path("<int:pk>/", views.server_detail, name="detail"),
]
