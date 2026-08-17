from django.contrib import admin


def configured_field(field, description):
    """创建只展示“是否已配置”的 Admin 布尔字段，避免回显秘密。"""

    @admin.display(description=description, boolean=True)
    def is_configured(_admin, obj):
        return bool(obj and getattr(obj, field))

    return is_configured
