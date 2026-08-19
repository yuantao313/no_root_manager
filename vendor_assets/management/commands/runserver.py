from django.contrib.staticfiles.management.commands.runserver import Command as RunServerCommand

from ._base import EnsureVendorAssetsMixin


class Command(EnsureVendorAssetsMixin, RunServerCommand):
    """开发服务器启动前无感补齐第三方资源。"""
