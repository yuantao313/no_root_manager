from django.contrib.staticfiles.management.commands.collectstatic import Command as CollectStaticCommand

from ._base import EnsureVendorAssetsMixin


class Command(EnsureVendorAssetsMixin, CollectStaticCommand):
    """收集静态文件前无感补齐第三方资源。"""
