"""GitCode OAuth2 视图适配器。"""

from allauth.socialaccount.providers.oauth2.views import (
    OAuth2Adapter,
    OAuth2CallbackView,
    OAuth2LoginView,
)

from .provider import GitCodeProvider


class GitCodeOAuth2Adapter(OAuth2Adapter):
    provider_id = GitCodeProvider.id
    access_token_url = "https://gitcode.com/oauth/token"
    authorize_url = "https://gitcode.com/oauth/authorize"
    profile_url = "https://api.gitcode.com/api/v5/user"

    def complete_login(self, request, app, token, **kwargs):
        headers = {"Authorization": f"Bearer {token.token}"}
        data = self.get_json(self.profile_url, headers=headers)
        return self.get_provider().sociallogin_from_response(request, data)


oauth2_login = OAuth2LoginView.adapter_view(GitCodeOAuth2Adapter)
oauth2_callback = OAuth2CallbackView.adapter_view(GitCodeOAuth2Adapter)
