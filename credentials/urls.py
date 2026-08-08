from django.urls import path

from . import views

app_name = "credentials"

urlpatterns = [
    path("", views.credential_list, name="list"),
    path("new/", views.credential_create, name="create"),
    path("<int:pk>/", views.credential_detail, name="detail"),
]
