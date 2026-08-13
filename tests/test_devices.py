"""设备信息查询测试：统一入口 get_device_info 的解析与 LRU 缓存（mock SSH，不连真实机器）。

实现方式：按设备类型上传执行仓库内置独立脚本（host_info.sh / npu_info.sh / gpu_info.sh
预留），分别解析结构化输出后合并。
"""

from unittest.mock import patch

import pytest

from credentials.models import Credential
from servers.models import Server

pytestmark = pytest.mark.django_db


@pytest.fixture
def npu_server():
    cred = Credential.objects.create(name="c1", username="root", password="p")
    return Server.objects.create(name="npu机", host="10.0.0.1", port=22, credential=cred, is_npu=True)


# host_info.sh 与 npu_info.sh 的典型输出（模拟真实机器：鲲鹏 CPU + 2 张 NPU 卡）
_HOST_OUT = """CPU_VENDOR=HiSilicon
CPU_MODEL=Kunpeng 920
CPU_FREQ_MHZ=2600
CPU_CORES=192
MEM_TOTAL=512G
DISK_ROOT=916G（已用 120G）
"""
_NPU_OUT = """NPU_CARD 0 128 Ascend 950 Pro
NPU_CARD 1 64 Ascend 910B
"""


def _fake_run_script(server, script, timeout=60, connect_timeout=8, **kwargs):
    if script.endswith("host_info.sh"):
        return True, _HOST_OUT, ""
    if script.endswith("npu_info.sh"):
        return True, _NPU_OUT, ""
    if script.endswith("gpu_info.sh"):
        return True, "GPU_ERROR=GPU 检测暂未实现（预留）", ""
    raise AssertionError(f"未预期的脚本：{script}")


def test_parse_host(npu_server):
    from servers.devices import _parse_host

    cpu, memory, disk = _parse_host(_HOST_OUT)
    assert cpu == "HiSilicon Kunpeng 920 @2.6GHz 192核"
    assert memory == "512G"
    assert disk == "916G（已用 120G）"


def test_parse_host_intel_hides_vendor(npu_server):
    """Intel 机器：GenuineIntel 冗余不显示（型号已含 Intel(R)），频率保留。"""
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


def test_parse_cards(npu_server):
    from servers.devices import _parse_cards

    cards, err = _parse_cards(_NPU_OUT, "NPU")
    assert cards == [
        {"index": 0, "soc_name": "Ascend 950 Pro", "mem_g": 128},
        {"index": 1, "soc_name": "Ascend 910B", "mem_g": 64},
    ]
    assert err == ""
    cards, err = _parse_cards("GPU_ERROR=暂未实现", "GPU")
    assert cards == [] and "暂未实现" in err


def test_collect_device_info_npu(npu_server):
    """NPU 服务器：host + npu 两个脚本分别执行并合并。"""
    from servers.devices import _collect_device_info

    with patch("servers.devices.run_script", side_effect=_fake_run_script) as m:
        info = _collect_device_info(npu_server)
    scripts = [c.args[1] for c in m.call_args_list]
    assert any(s.endswith("host_info.sh") for s in scripts)
    assert any(s.endswith("npu_info.sh") for s in scripts)
    assert not any(s.endswith("gpu_info.sh") for s in scripts)  # GPU 预留不执行
    assert info["npu"][0]["soc_name"] == "Ascend 950 Pro"
    assert info["npu"][1]["mem_g"] == 64
    assert info["gpu"] == []
    assert info["cpu"] == "HiSilicon Kunpeng 920 @2.6GHz 192核"
    assert info["memory"] == "512G"
    assert info["disk"] == "916G（已用 120G）"


def test_collect_device_info_non_npu(npu_server):
    """非 NPU 服务器：只跑 host_info.sh，不跑 npu_info.sh。"""
    from servers.devices import _collect_device_info

    npu_server.is_npu = False
    with patch("servers.devices.run_script", side_effect=_fake_run_script) as m:
        info = _collect_device_info(npu_server)
    scripts = [c.args[1] for c in m.call_args_list]
    assert any(s.endswith("host_info.sh") for s in scripts)
    assert not any(s.endswith("npu_info.sh") for s in scripts)
    assert info["npu"] == []


def test_collect_device_info_failure(npu_server):
    """脚本执行失败：返回空设备信息 + 错误信息（不影响分组接口）。"""
    from servers.devices import _collect_device_info

    with patch("servers.devices.run_script", return_value=(False, "", "连接失败")):
        info = _collect_device_info(npu_server)
    assert info["npu"] == []
    assert "连接失败" in info["msg"]


def test_device_info_cache(npu_server):
    """TTL 缓存：同服务器二次查询不再执行脚本；失败结果可被清理后重查。"""
    from servers.devices import clear_device_info_cache, get_device_info

    clear_device_info_cache()
    with patch("servers.devices.run_script", side_effect=_fake_run_script) as m:
        get_device_info(npu_server)
        first_calls = len(m.call_args_list)
        get_device_info(npu_server)
    assert len(m.call_args_list) == first_calls  # 二次命中缓存，无新增调用
    # 清缓存后重新查询：会再次执行脚本（失败结果不再被永久钉死）
    clear_device_info_cache()
    with patch("servers.devices.run_script", side_effect=_fake_run_script) as m2:
        get_device_info(npu_server)
    assert len(m2.call_args_list) > 0


def test_device_info_failure_not_pinned(npu_server):
    """失败结果不永久缓存：负缓存过期后重新查询可拿到新数据（修复 CPU 空白钉死）。"""
    from servers.devices import clear_device_info_cache, get_device_info

    clear_device_info_cache()
    # 第一次：脚本失败（目标机不可达）→ 无快照时返回空 + msg
    with patch("servers.devices.run_script", return_value=(False, "", "连接失败")):
        info1 = get_device_info(npu_server)
    assert info1["cpu"] == "" and "连接失败" in info1["msg"]
    # 清缓存 + 目标机恢复：重新查询能拿到数据，并落库快照
    clear_device_info_cache()
    with patch("servers.devices.run_script", side_effect=_fake_run_script):
        info2 = get_device_info(npu_server)
    assert info2["cpu"] == "HiSilicon Kunpeng 920 @2.6GHz 192核"
    npu_server.refresh_from_db()
    assert npu_server.device_info_snapshot.get("cpu") == info2["cpu"]
    assert npu_server.device_info_updated_at is not None


def test_device_info_fallback_to_snapshot(npu_server):
    """目标机不可达时回退数据库快照展示（页面不空白，并注明来源）。"""
    from servers.devices import clear_device_info_cache, get_device_info

    # 先成功采集一次，快照落库
    clear_device_info_cache()
    with patch("servers.devices.run_script", side_effect=_fake_run_script):
        get_device_info(npu_server)
    npu_server.refresh_from_db()
    assert npu_server.device_info_snapshot.get("cpu")
    # 目标机不可达：回退快照，页面仍有数据
    clear_device_info_cache()
    with patch("servers.devices.run_script", return_value=(False, "", "连接失败")):
        info = get_device_info(npu_server)
    assert info["cpu"] == "HiSilicon Kunpeng 920 @2.6GHz 192核"
    assert "最近一次成功采集" in info["msg"]


def test_device_info_gpu_reserved(npu_server):
    """GPU 预留：结构上未实现，仅验证调用不炸。"""
    from servers.devices import get_device_info

    with patch("servers.devices.run_script", side_effect=_fake_run_script):
        info = get_device_info(npu_server)
    assert "npu" in info and "gpu" in info


def test_get_device_info_cached_no_ssh(npu_server):
    """申请页只读模式：绝不触发 SSH（run_script 不被调用），优先返回数据库快照。"""
    from servers.devices import clear_device_info_cache, get_device_info_cached

    clear_device_info_cache()
    # 先实时采集一次，快照落库
    with patch("servers.devices.run_script", side_effect=_fake_run_script):
        from servers.devices import get_device_info

        get_device_info(npu_server)
    npu_server.refresh_from_db()
    assert npu_server.device_info_snapshot.get("cpu")
    # 清内存缓存后走只读模式：应返回快照数据，且不再调用 run_script（不 SSH）
    clear_device_info_cache()
    with patch("servers.devices.run_script", side_effect=AssertionError("只读模式不应触发 SSH")) as m2:
        info = get_device_info_cached(npu_server)
    assert m2.call_count == 0
    assert info["cpu"] == "HiSilicon Kunpeng 920 @2.6GHz 192核"
    assert info["npu"][0]["soc_name"] == "Ascend 950 Pro"


def test_get_device_info_cached_empty_no_ssh(npu_server):
    """无缓存无快照：只读模式返回空设备信息，不报错、不 SSH。"""
    from servers.devices import clear_device_info_cache, get_device_info_cached

    clear_device_info_cache()
    npu_server.device_info_snapshot = {}
    npu_server.save(update_fields=["device_info_snapshot"])
    with patch("servers.devices.run_script", side_effect=AssertionError("只读模式不应触发 SSH")):
        info = get_device_info_cached(npu_server)
    assert info["npu"] == []
    assert info["cpu"] == ""
    assert info["msg"] == ""
