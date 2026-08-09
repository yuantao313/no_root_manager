"""GitCode OAuth2 登录/回调视图适配（adapter 定义见 provider.py）。"""

from allauth.socialaccount.providers.oauth2.views import (
    OAuth2CallbackView,
    OAuth2LoginView,
)

from .provider import GitCodeOAuth2Adapter

oauth2_login = OAuth2LoginView.adapter_view(GitCodeOAuth2Adapter)
oauth2_callback = OAuth2CallbackView.adapter_view(GitCodeOAuth2Adapter)
