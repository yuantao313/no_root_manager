"""基础设备信息采集、缓存与快照回退测试（mock SSH）。"""

from unittest.mock import patch

import pytest

from credentials.models import Credential
from servers.models import Server

pytestmark = pytest.mark.django_db


@pytest.fixture
def server():
    cred = Credential.objects.create(name="c1", username="root", password="p")
    return Server.objects.create(name="测试机", host="10.0.0.1", port=22, credential=cred)


_HOST_OUT = """CPU_VENDOR=HiSilicon
CPU_MODEL=Kunpeng 920
CPU_FREQ_MHZ=2600
CPU_CORES=192
MEM_TOTAL=512G
DISK_ROOT=916G（已用 120G）
"""


def _fake_run_script(server, script, timeout=60, connect_timeout=8, **kwargs):
    assert script.endswith("host_info.sh")
    return True, _HOST_OUT, ""


def test_parse_host(server):
    from servers.devices import _parse_host

    cpu, memory, disk = _parse_host(_HOST_OUT)
    assert cpu == "HiSilicon Kunpeng 920 @2.6GHz 192核"
    assert memory == "512G"
    assert disk == "916G（已用 120G）"


def test_parse_host_intel_hides_vendor(server):
    from servers.devices import _parse_host

    out = (
        "CPU_VENDOR=GenuineIntel\n"
        "CPU_MODEL=Intel(R) Xeon(R) Platinum 8480C\n"
        "CPU_FREQ_MHZ=3200\n"
        "CPU_CORES=32\n"
        "MEM_TOTAL=256G\n"
        "DISK_ROOT=500G（已用 80G）\n"
    )
    cpu, _, _ = _parse_host(out)
    assert cpu == "Intel(R) Xeon(R) Platinum 8480C @3.2GHz 32核"


def test_collect_device_info(server):
    from servers.devices import _collect_device_info

    with patch("servers.devices.run_script", side_effect=_fake_run_script) as mocked:
        info = _collect_device_info(server)
    assert mocked.call_count == 1
    assert info == {
        "cpu": "HiSilicon Kunpeng 920 @2.6GHz 192核",
        "memory": "512G",
        "disk": "916G（已用 120G）",
        "msg": "",
    }


def test_collect_device_info_failure(server):
    from servers.devices import _collect_device_info

    with patch("servers.devices.run_script", return_value=(False, "", "连接失败")):
        info = _collect_device_info(server)
    assert info["cpu"] == ""
    assert "连接失败" in info["msg"]


def test_device_info_cache(server):
    from servers.devices import clear_device_info_cache, get_device_info

    clear_device_info_cache()
    with patch("servers.devices.run_script", side_effect=_fake_run_script) as mocked:
        get_device_info(server)
        get_device_info(server)
    assert mocked.call_count == 1


def test_device_info_failure_not_pinned(server):
    from servers.devices import clear_device_info_cache, get_device_info

    clear_device_info_cache()
    with patch("servers.devices.run_script", return_value=(False, "", "连接失败")):
        info = get_device_info(server)
    assert info["cpu"] == "" and "连接失败" in info["msg"]

    clear_device_info_cache()
    with patch("servers.devices.run_script", side_effect=_fake_run_script):
        recovered = get_device_info(server)
    server.refresh_from_db()
    assert recovered["cpu"] == "HiSilicon Kunpeng 920 @2.6GHz 192核"
    assert server.device_info_snapshot["cpu"] == recovered["cpu"]
    assert server.device_info_updated_at is not None


def test_device_info_fallback_to_snapshot(server):
    from servers.devices import clear_device_info_cache, get_device_info

    clear_device_info_cache()
    with patch("servers.devices.run_script", side_effect=_fake_run_script):
        get_device_info(server)
    clear_device_info_cache()
    with patch("servers.devices.run_script", return_value=(False, "", "连接失败")):
        info = get_device_info(server)
    assert info["cpu"] == "HiSilicon Kunpeng 920 @2.6GHz 192核"
    assert "最近一次成功采集" in info["msg"]
