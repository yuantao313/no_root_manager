"""自定义 SocialAccountAdapter：控制 GitCode 首次登录流程。"""

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class GitCodeSocialAccountAdapter(DefaultSocialAccountAdapter):
    """首次 GitCode 登录（无绑定用户）必须经过 signup 确认页。

    - 已绑定用户（SocialAccount 按 provider+uid 命中）：直接登录，不经过 signup
    - 首次登录（无对应用户）：进入 signup 页，可选择"创建新账号"或"绑定已有账号"
    """

    def is_auto_signup_allowed(self, request, sociallogin):
        # 关闭自动建号：首次登录必须由用户在本页确认（注册或绑定），
        # 避免以 GitCode 默认信息静默创建裸账号
        return False
