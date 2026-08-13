from django.urls import path

from . import views

app_name = "applications"

urlpatterns = [
    path("", views.application_list, name="list"),
    path("my/", views.my_applications, name="my"),
    path("<int:pk>/withdraw/", views.application_withdraw, name="withdraw"),
    path("<int:pk>/resend-mail/", views.application_resend_mail, name="resend_mail"),
    path("<int:pk>/", views.application_detail, name="detail"),
    path("<int:pk>/review/<str:action>/", views.application_review, name="review"),
]
