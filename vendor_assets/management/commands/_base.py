from django.core.management.base import CommandError

from scripts.ensure_vendor_assets import ensure_assets


class EnsureVendorAssetsMixin:
    """在 Django 静态资源命令执行前自动补齐固定版本三方文件。"""

    def handle(self, *args, **options):
        try:
            present, downloaded = ensure_assets()
        except Exception as error:  # noqa: BLE001 - 转换成 Django 友好的命令错误
            raise CommandError(f"第三方静态资源准备失败：{error}") from error
        self.stdout.write(f"第三方静态资源就绪：本地 {present}，下载 {downloaded}。")
        return super().handle(*args, **options)
