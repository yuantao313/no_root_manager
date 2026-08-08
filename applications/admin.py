from django.contrib import admin

from .models import Application, SudoGrant


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("title", "applicant_name", "username", "status", "target_server", "created_at")
    list_filter = ("status", "apply_type")
    search_fields = ("title", "applicant_name", "username", "email")


@admin.register(SudoGrant)
class SudoGrantAdmin(admin.ModelAdmin):
    list_display = ("username", "server", "status", "granted_by", "granted_at", "expires_at", "revoked_at")
    list_filter = ("status",)
    search_fields = ("username", "server__name")
    readonly_fields = ("granted_at",)
