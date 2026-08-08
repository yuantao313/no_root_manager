"""GitCode provider 登录/回调路由（allauth 标准生成）。"""

from allauth.socialaccount.providers.oauth2.urls import default_urlpatterns

from .provider import GitCodeProvider

urlpatterns = default_urlpatterns(GitCodeProvider)
