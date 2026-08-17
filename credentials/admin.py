from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from config.admin import configured_field

from .forms import CredentialForm
from .models import Credential


@admin.register(Credential)
class CredentialAdmin(admin.ModelAdmin):
    form = CredentialForm
    list_display = ("name", "username", "has_password", "has_private_key", "updated_at")
    list_filter = ("username",)
    search_fields = ("name", "username", "remark")
    readonly_fields = ("has_password", "has_private_key", "created_at", "updated_at")
    fields = (
        "name",
        "username",
        "remark",
        "password",
        "private_key",
        "has_password",
        "has_private_key",
        "created_at",
        "updated_at",
    )

    has_password = configured_field("password", "密码")
    has_private_key = configured_field("private_key", "私钥")

    @staticmethod
    def _management_redirect():
        return redirect(f"{reverse('servers:list')}?tab=credentials")

    def _return_redirect(self, request):
        """仅接受本站 next，支持从服务器表单新增凭据后返回原流程。"""
        next_url = request.GET.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return self._management_redirect()

    def response_add(self, request, obj, post_url_continue=None):
        if "_continue" in request.POST or "_addanother" in request.POST:
            return super().response_add(request, obj, post_url_continue)
        return self._return_redirect(request)

    def response_change(self, request, obj):
        if "_continue" in request.POST or "_addanother" in request.POST:
            return super().response_change(request, obj)
        return self._return_redirect(request)

    def response_delete(self, request, obj_display, obj_id):
        return self._return_redirect(request)
