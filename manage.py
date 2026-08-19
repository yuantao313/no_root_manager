#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def prepare_vendor_assets(argv):
    """开发启动或收集静态文件前，缺什么才下载什么。"""
    if len(argv) < 2 or argv[1] not in {"runserver", "collectstatic"}:
        return None
    from scripts.ensure_vendor_assets import ensure_assets

    return ensure_assets()


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        asset_result = prepare_vendor_assets(sys.argv)
    except Exception as exc:  # noqa: BLE001 - 启动入口需要给出明确失败原因
        raise SystemExit(f"第三方静态资源准备失败：{exc}") from exc
    if asset_result:
        present, downloaded = asset_result
        print(f"第三方静态资源就绪：本地 {present}，下载 {downloaded}。")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
