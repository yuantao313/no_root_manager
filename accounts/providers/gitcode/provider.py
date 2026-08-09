"""GitCode 自定义 OAuth2 Provider（django-allauth）。

GitCode 用户 id 为 24 位十六进制字符串（ObjectId 风格），
以 uid 映射系统账号（不使用 login，防修改与匿名）。

Adapter 定义在本模块（而非 views.py），避免 provider 与视图循环导入。
"""

from allauth.socialaccount.providers.base import ProviderAccount
from allauth.socialaccount.providers.oauth2.provider import OAuth2Provider
from allauth.socialaccount.providers.oauth2.views import OAuth2Adapter


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

    def complete_login(self, request, app, token, **kwargs):
        headers = {"Authorization": f"Bearer {token.token}"}
        data = self.get_json(self.profile_url, headers=headers)
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
            "email": data.get("email") or "",
            "first_name": data.get("name") or "",
        }


provider_classes = [GitCodeProvider]
