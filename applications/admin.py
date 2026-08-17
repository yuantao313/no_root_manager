from django.contrib import admin

from .models import Application


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    exclude = ("initial_password",)
    list_display = ("title", "applicant_name", "username", "status", "target_server", "created_at")
    list_filter = ("status", "apply_type")
    search_fields = ("title", "applicant_name", "username", "email")
