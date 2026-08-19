from django import template

register = template.Library()


@register.filter
def user_label(user, fallback_name=""):
    """将平台用户显示为“姓名（用户名）”，可回退到历史姓名快照。"""
    if not user:
        return ""
    username = (getattr(user, "username", "") or "").strip()
    full_name = (user.get_full_name() or fallback_name or "").strip()
    return f"{full_name}（{username}）" if full_name and full_name != username else username
