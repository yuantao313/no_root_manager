"""GitCode 自定义 OAuth2 Provider（django-allauth）。

GitCode 用户 id 为 24 位十六进制字符串（ObjectId 风格），
以 uid 映射系统账号（不使用 login，防修改与匿名）。

Adapter 定义在本模块（而非 views.py），避免 provider 与视图循环导入。
"""

import requests
from allauth.socialaccount.providers.base import ProviderAccount
from allauth.socialaccount.providers.oauth2.provider import OAuth2Provider
from allauth.socialaccount.providers.oauth2.views import OAuth2Adapter
from django.conf import settings
from django.urls import reverse


class GitCodeAccount(ProviderAccount):
    def get_profile_url(self):
        return self.account.extra_data.get("web_url") or ""

    def to_str(self):
        dflt = super().to_str()
        return self.account.extra_data.get("name") or dflt


class GitCodeOAuth2Adapter(OAuth2Adapter):
    provider_id = "gitcode"
    access_token_url = "https://gitcode.com/oauth/token"
    authorize_url = "https://gitcode.com/oauth/authorize"
    profile_url = "https://api.gitcode.com/api/v5/user"

    def get_callback_url(self, request, app):
        """固定回调地址：使用系统设置的站点基准地址，不随请求 host 漂移。

        优先取 SystemConfig.site_base_url（数据库配置），留空时回退
        settings.GITCODE_CALLBACK_BASE_URL；否则 GitCode 应用管理页配置的
        回调地址与实际生成的 redirect_uri 不一致，GitCode 会返回"回调不匹配"。
        """
        try:
            from accounts.models import SystemConfig

            base = SystemConfig.get_singleton().get_site_base_url()
        except Exception:  # noqa: BLE001 —— 配置读取失败时退回默认逻辑
            base = getattr(settings, "GITCODE_CALLBACK_BASE_URL", "").rstrip("/")
        callback_path = reverse(f"{self.provider_id}_callback")
        if base:
            return f"{base}{callback_path}"
        return super().get_callback_url(request, app)

    def complete_login(self, request, app, token, **kwargs):
        # allauth 65.x 的 OAuth2Adapter 无 get_json，使用 requests 获取用户信息
        headers = {"Authorization": f"Bearer {token.token}"}
        resp = requests.get(self.profile_url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return self.get_provider().sociallogin_from_response(request, data)


class GitCodeProvider(OAuth2Provider):
    id = "gitcode"
    name = "GitCode"
    account_class = GitCodeAccount
    oauth2_adapter_class = GitCodeOAuth2Adapter

    def get_default_scope(self):
        return ["all_user"]

    def extract_uid(self, data):
        # GitCode 用户 id（唯一映射依据，24 位 hex 字符串）
        return str(data["id"])

    def extract_common_fields(self, data):
        return {
            # 用户名用 GitCode 用户 id 映射（gc<id>），不用 login（可改名，防映射失效）
            "username": f"gc{data.get('id', '')}",
            "email": data.get("email") or "",
            "first_name": data.get("name") or "",
        }


provider_classes = [GitCodeProvider]
