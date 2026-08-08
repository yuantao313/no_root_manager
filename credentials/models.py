from django.db import models

from servers.fields import EncryptedTextField


class Credential(models.Model):
    """管理凭据：记录目标服务器的登录用户名、密码/密钥及备注，敏感字段加密存储。"""

    name = models.CharField("凭据名称", max_length=100, help_text="用于识别的名称，如：生产环境 root")
    username = models.CharField("用户名", max_length=100)
    # 密码与私钥二选一（或都填），均加密落库
    password = EncryptedTextField("密码", blank=True, help_text="登录密码，存储时加密")
    private_key = EncryptedTextField("私钥/密钥", blank=True, help_text="SSH 私钥内容，存储时加密")
    remark = models.TextField("备注", blank=True)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "管理凭据"
        verbose_name_plural = "管理凭据"

    def __str__(self):
        return f"{self.name} ({self.username})"
