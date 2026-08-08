# 用户管理完全复用 Django 内置 auth 机制：
# - 模型：django.contrib.auth.models.User（is_staff / is_superuser）
# - 后台：django.contrib.auth.admin.UserAdmin（auth 应用已默认注册）
# 不添加任何自定义字段、表单或权限判断机制。
