#!/usr/bin/env python3
"""检查并补齐固定版本的前端第三方静态资源。"""

import argparse
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_ASSET_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Asset:
    path: str
    url: str
    sha256: str


ASSETS = (
    Asset(
        "static/vendor/bootstrap/3.4.1/css/bootstrap.min.css",
        "https://cdn.jsdelivr.net/npm/bootstrap@3.4.1/dist/css/bootstrap.min.css",
        "6d92dfc1700fd38cd130ad818e23bc8aef697f815b2ea5face2b5dfad22f2e11",
    ),
    Asset(
        "static/vendor/bootstrap/3.4.1/js/bootstrap.min.js",
        "https://cdn.jsdelivr.net/npm/bootstrap@3.4.1/dist/js/bootstrap.min.js",
        "9ee2fcff6709e4d0d24b09ca0fc56aade12b4961ed9c43fd13b03248bfb57afe",
    ),
    Asset(
        "static/vendor/bootstrap/3.4.1/LICENSE",
        "https://cdn.jsdelivr.net/npm/bootstrap@3.4.1/LICENSE",
        "0f9f7ff25c98790a39d5c70b785e9fa0a8d276d45e78c7559502085ccaab8209",
    ),
    Asset(
        "static/vendor/bootstrap/3.4.1/fonts/glyphicons-halflings-regular.eot",
        "https://cdn.jsdelivr.net/npm/bootstrap@3.4.1/dist/fonts/glyphicons-halflings-regular.eot",
        "13634da87d9e23f8c3ed9108ce1724d183a39ad072e73e1b3d8cbf646d2d0407",
    ),
    Asset(
        "static/vendor/bootstrap/3.4.1/fonts/glyphicons-halflings-regular.svg",
        "https://cdn.jsdelivr.net/npm/bootstrap@3.4.1/dist/fonts/glyphicons-halflings-regular.svg",
        "42f60659d265c1a3c30f9fa42abcbb56bd4a53af4d83d316d6dd7a36903c43e5",
    ),
    Asset(
        "static/vendor/bootstrap/3.4.1/fonts/glyphicons-halflings-regular.ttf",
        "https://cdn.jsdelivr.net/npm/bootstrap@3.4.1/dist/fonts/glyphicons-halflings-regular.ttf",
        "e395044093757d82afcb138957d06a1ea9361bdcf0b442d06a18a8051af57456",
    ),
    Asset(
        "static/vendor/bootstrap/3.4.1/fonts/glyphicons-halflings-regular.woff",
        "https://cdn.jsdelivr.net/npm/bootstrap@3.4.1/dist/fonts/glyphicons-halflings-regular.woff",
        "a26394f7ede100ca118eff2eda08596275a9839b959c226e15439557a5a80742",
    ),
    Asset(
        "static/vendor/bootstrap/3.4.1/fonts/glyphicons-halflings-regular.woff2",
        "https://cdn.jsdelivr.net/npm/bootstrap@3.4.1/dist/fonts/glyphicons-halflings-regular.woff2",
        "fe185d11a49676890d47bb783312a0cda5a44c4039214094e7957b4c040ef11c",
    ),
    Asset(
        "static/vendor/jquery/1.11.3/jquery.min.js",
        "https://cdn.jsdelivr.net/npm/jquery@1.11.3/dist/jquery.min.js",
        "aec3d419d50f05781a96f223e18289aeb52598b5db39be82a7b71dc67d6a7947",
    ),
    Asset(
        "static/vendor/jquery/1.11.3/MIT-LICENSE.txt",
        "https://cdn.jsdelivr.net/npm/jquery@1.11.3/MIT-LICENSE.txt",
        "44254c9a91a4647b9192584f17b2d27bf43696ba1ea16b0e88ac7bd6bf0780f1",
    ),
    Asset(
        "static/vendor/select2/4.1.0-rc.0/css/select2.min.css",
        "https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/css/select2.min.css",
        "cda4a81c187015d95ed2c71f1841540b08203cdec5fa2a7d5d1825a3c2166f8c",
    ),
    Asset(
        "static/vendor/select2/4.1.0-rc.0/js/select2.min.js",
        "https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/js/select2.min.js",
        "f7244fff610595b944f76bf3080d74e3af42b5dd234f8f079e698cc39ac966b0",
    ),
    Asset(
        "static/vendor/select2/4.1.0-rc.0/LICENSE.md",
        "https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/LICENSE.md",
        "4ee0cbc51370afde358652a0f977972053729ed578b6a42f5e2a037d114f0b39",
    ),
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_asset_once(asset: Asset, root: Path) -> None:
    """下载到同目录临时文件，校验通过后原子替换目标文件。"""
    destination = root / asset.path
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(asset.url, headers={"User-Agent": "NRM-vendor-assets/1"})
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(fd, "wb") as target, urlopen(request, timeout=30) as response:
            while chunk := response.read(64 * 1024):
                total += len(chunk)
                if total > MAX_ASSET_BYTES:
                    raise RuntimeError(f"资源超过 {MAX_ASSET_BYTES} 字节限制：{asset.url}")
                digest.update(chunk)
                target.write(chunk)
        if digest.hexdigest() != asset.sha256:
            raise RuntimeError(f"资源校验失败：{asset.url}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def download_asset(asset: Asset, root: Path) -> None:
    """短暂网络故障最多重试三次；内容或大小校验失败不重试。"""
    last_error = None
    for _attempt in range(3):
        try:
            _download_asset_once(asset, root)
            return
        except (TimeoutError, URLError) as error:
            last_error = error
    raise RuntimeError(f"下载资源失败：{asset.url}") from last_error


def ensure_assets(root: Path = PROJECT_ROOT, *, check_only: bool = False) -> tuple[int, int]:
    """返回（已存在数量，已下载数量）；check_only 下缺失或损坏直接报错。"""
    present = 0
    downloaded = 0
    invalid = []
    for asset in ASSETS:
        destination = root / asset.path
        if destination.is_file() and file_sha256(destination) == asset.sha256:
            present += 1
            continue
        if check_only:
            invalid.append(asset.path)
            continue
        download_asset(asset, root)
        downloaded += 1
    if invalid:
        raise RuntimeError("第三方静态资源缺失或校验失败：" + "、".join(invalid))
    return present, downloaded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查本地文件，不访问网络")
    args = parser.parse_args()
    try:
        present, downloaded = ensure_assets(check_only=args.check)
    except Exception as error:  # noqa: BLE001 - 命令行入口统一转换为明确错误
        parser.exit(1, f"错误：{error}\n")
    print(f"第三方静态资源就绪：本地 {present}，下载 {downloaded}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
