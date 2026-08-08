from django.conf import settings
from django.db import models


class GitCodeBinding(models.Model):
    """用户与 GitCode 账号的绑定关系（用户 id 映射，不修改 auth 用户模型）。

    - 已注册用户可主动绑定自己的 GitCode 账号
    - GitCode OAuth 登录创建的用户（gc<id>）也通过本表记录映射
    - gitcode_id 唯一：一个 GitCode 账号只能绑定一个系统用户
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gitcode_binding",
        verbose_name="用户",
    )
    gitcode_id = models.CharField(
        "GitCode 用户 id",
        max_length=100,
        unique=True,
        help_text="GitCode 用户唯一 id（24 位十六进制字符串，非数字），作为映射依据（login 可变，id 不变）",
    )
    gitcode_username = models.CharField(
        "GitCode 用户名", max_length=100, blank=True, help_text="仅展示用"
    )
    created_at = models.DateTimeField("绑定时间", auto_now_add=True)

    class Meta:
        verbose_name = "GitCode 绑定"
        verbose_name_plural = "GitCode 绑定"

    def __str__(self):
        return f"{self.user.username} → gitcode#{self.gitcode_id}"
