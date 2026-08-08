"""用户管理：复用 Django 内置 auth 用户模型与管理机制。

在 Django admin 中用一个"管理员"字段（is_manager）同时驱动
is_staff 与 is_superuser，不新增任何自定义模型字段。

实现要点：
- is_manager 作为表单类属性声明（进入 declared_fields，Django 元类会收集）
- UserAdmin.fieldsets = None，让 ModelAdmin 不按 fieldsets 过滤表单字段，
  改由表单的 Meta 决定字段（fields=__all__ 且 exclude 掉两个底层字段）
"""

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import User

_MANAGER_HELP = "勾选后同时获得后台访问权限（is_staff）与超级用户权限（is_superuser）"


class ManagerUserCreationForm(UserCreationForm):
    """创建用户：单个"管理员"字段，保存时同步 is_staff/is_superuser。"""

    is_manager = forms.BooleanField(required=False, label="管理员", help_text=_MANAGER_HELP)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["is_manager"].initial = self.instance.is_superuser

    def save(self, commit=True):
        user = super().save(commit=False)
        is_manager = self.cleaned_data.get("is_manager", False)
        user.is_staff = is_manager
        user.is_superuser = is_manager
        if commit:
            user.save()
        return user


class ManagerUserChangeForm(UserChangeForm):
    """编辑用户：隐藏 is_staff/is_superuser，用单个"管理员"字段替代。"""

    is_manager = forms.BooleanField(required=False, label="管理员", help_text=_MANAGER_HELP)

    class Meta(UserChangeForm.Meta):
        # fields=__all__ 继承自 UserChangeForm；这里排除底层两字段，
        # is_manager 作为 declared_fields 自动合并进表单
        fields = None
        exclude = ("is_staff", "is_superuser")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["is_manager"].initial = self.instance.is_superuser

    def save(self, commit=True):
        user = super().save(commit=False)
        is_manager = self.cleaned_data.get("is_manager", False)
        user.is_staff = is_manager
        user.is_superuser = is_manager
        if commit:
            user.save()
        return user


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = ManagerUserCreationForm
    form = ManagerUserChangeForm

    # fieldsets=None：让表单的 Meta 决定字段（避免 admin 按 fieldsets
    # 过滤导致 is_manager 这类非模型字段报 Unknown field）
    fieldsets = None

    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "password1", "password2", "is_manager")}),
    )

    def get_fieldsets(self, request, obj=None):
        if not obj:
            return self.add_fieldsets
        return [
            (None, {"fields": ("username", "password")}),
            ("个人信息", {"fields": ("first_name", "last_name", "email")}),
            ("权限", {"fields": ("is_manager", "groups", "user_permissions")}),
            ("重要日期", {"fields": ("last_login", "date_joined")}),
        ]

    list_display = ("username", "email", "is_manager", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")

    @admin.display(description="管理员", boolean=True)
    def is_manager(self, obj):
        # 以 is_superuser 为准（is_staff 由同一表单字段同步）
        return obj.is_superuser
