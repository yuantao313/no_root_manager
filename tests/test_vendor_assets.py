from hashlib import sha256
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

import pytest
from django.contrib.staticfiles.management.commands.collectstatic import Command as DjangoCollectStaticCommand
from django.core.management import get_commands

from scripts.ensure_vendor_assets import ASSETS, Asset, download_asset, ensure_assets
from vendor_assets.management.commands.collectstatic import Command as NRMCollectStaticCommand


class _Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _asset(content: bytes) -> Asset:
    return Asset("static/vendor/test.js", "https://cdn.example.test/test.js", sha256(content).hexdigest())


def test_checked_in_vendor_assets_match_locked_digests():
    present, downloaded = ensure_assets(check_only=True)

    assert present == len(ASSETS)
    assert downloaded == 0


def test_django_static_commands_use_vendor_assets_overrides():
    get_commands.cache_clear()
    commands = get_commands()

    assert commands["collectstatic"] == "vendor_assets"
    assert commands["runserver"] == "vendor_assets"


def test_collectstatic_prepares_vendor_assets_before_parent_command():
    with (
        patch("vendor_assets.management.commands._base.ensure_assets", return_value=(12, 1)) as ensure,
        patch.object(DjangoCollectStaticCommand, "handle", return_value="collected") as parent,
    ):
        result = NRMCollectStaticCommand().handle(dry_run=True)

    assert result == "collected"
    ensure.assert_called_once_with()
    parent.assert_called_once_with(dry_run=True)


def test_existing_valid_asset_does_not_access_network(tmp_path):
    content = b"local asset"
    asset = _asset(content)
    destination = tmp_path / asset.path
    destination.parent.mkdir(parents=True)
    destination.write_bytes(content)

    with (
        patch("scripts.ensure_vendor_assets.ASSETS", (asset,)),
        patch("scripts.ensure_vendor_assets.urlopen") as request,
    ):
        assert ensure_assets(tmp_path) == (1, 0)

    request.assert_not_called()


def test_missing_asset_is_downloaded_and_verified_atomically(tmp_path):
    content = b"downloaded asset"
    asset = _asset(content)
    with (
        patch("scripts.ensure_vendor_assets.ASSETS", (asset,)),
        patch("scripts.ensure_vendor_assets.urlopen", return_value=_Response(content)) as request,
    ):
        assert ensure_assets(tmp_path) == (0, 1)

    assert (tmp_path / asset.path).read_bytes() == content
    request.assert_called_once()


def test_invalid_download_does_not_replace_existing_file(tmp_path):
    expected = b"expected asset"
    asset = _asset(expected)
    destination = tmp_path / asset.path
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing invalid asset")

    with patch("scripts.ensure_vendor_assets.urlopen", return_value=_Response(b"tampered")):
        with pytest.raises(RuntimeError, match="资源校验失败"):
            download_asset(asset, tmp_path)

    assert destination.read_bytes() == b"existing invalid asset"
    assert list(destination.parent.glob(f".{destination.name}.*")) == []


def test_transient_download_error_is_retried(tmp_path):
    content = b"downloaded after retry"
    asset = _asset(content)
    with patch(
        "scripts.ensure_vendor_assets.urlopen",
        side_effect=[URLError("temporary failure"), _Response(content)],
    ) as request:
        download_asset(asset, tmp_path)

    assert (tmp_path / asset.path).read_bytes() == content
    assert request.call_count == 2


def test_check_only_reports_missing_asset_without_network(tmp_path):
    asset = _asset(b"expected")
    with (
        patch("scripts.ensure_vendor_assets.ASSETS", (asset,)),
        patch("scripts.ensure_vendor_assets.urlopen") as request,
        pytest.raises(RuntimeError, match="static/vendor/test.js"),
    ):
        ensure_assets(tmp_path, check_only=True)

    request.assert_not_called()
