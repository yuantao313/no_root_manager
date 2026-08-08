from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("webhooks/<int:pk>/delete/", views.webhook_delete, name="delete"),
]
