"""npu-smi info 解析测试：目标机原始表格文本 → 结构化卡片信息（mock 文本，不连真实机器）。

解析器 servers/npu_smi.py 供设备信息采集（devices.py）与卡组检测
（management.detect_npu_groups）复用，覆盖：正常多卡、健康状态、进程表
不误解析、Bus-Id 补充行、NPU_ERROR 降级、空输出。
"""

from servers.npu_smi import parse_npu_smi_info

# 参考真实 npu-smi info 输出：2 张卡（OK / Alarm）+ 进程表（首列也是数字，不应解析为卡）
_NPU_OUT = """+-----------------------------------------------------------------+
| npu-smi 25.7.rc1.7                               Version: 25.7.rc1.7 |
+--------+------------------+---------------+-------------------------+
| NPU ID | Name             | Health        | Power(W)  Temp(C)  Huge  |
|        |                  | Bus-Id        | NPU Util(%) Mem  HBM(MB) |
+========+==================+===============+=========================+
| 0      | Ascend950PR      | OK            | 276.0     69     0 / 0   |
|        |                  | 0000:71:00.0  | 99        0 / 0  8824 / 131072 |
+========+==================+===============+=========================+
| 1      | Ascend950PR      | Alarm         | 279.1     74     0 / 0   |
|        |                  | 0000:61:00.0  | 100       0 / 0  10769 / 131072 |
+========+==================+===============+=========================+
+----------------+---------------+-------------------------------------+
| NPU ID         | Process id    | Process name        | Process memory |
+================+===============+=====================================+
| 0              | 2066378       | pytest              | 360            |
+================+===============+=====================================+
"""


def test_parse_basic_cards():
    cards, err = parse_npu_smi_info(_NPU_OUT)
    assert err == ""
    assert len(cards) == 2
    c0, c1 = cards
    assert c0["index"] == 0
    assert c0["soc_name"] == "Ascend950PR"
    assert c0["health"] == "OK"
    assert c0["power_w"] == 276.0
    assert c0["temp_c"] == 69
    assert c0["util_pct"] == 99
    assert c0["bus_id"] == "0000:71:00.0"
    # HBM 总内存 131072 MB → 128 GB
    assert c0["hbm_used_mb"] == 8824
    assert c0["hbm_total_mb"] == 131072
    assert c0["mem_g"] == 128
    assert c1["index"] == 1
    assert c1["health"] == "Alarm"
    assert c1["util_pct"] == 100
    assert c1["hbm_used_mb"] == 10769
    assert c1["mem_g"] == 128


def test_parse_ignores_process_table():
    """进程表行首列也是数字（如 0/2066378/pytest），不得被误解析为卡。"""
    cards, _ = parse_npu_smi_info(_NPU_OUT)
    assert len(cards) == 2
    # 若误把进程行当卡，cards 会多出若干张 index 0 的伪卡


def test_parse_npu_error():
    cards, err = parse_npu_smi_info("NPU_ERROR=未找到 npu-smi（Ascend 驱动未安装或不在 PATH）")
    assert cards == []
    assert "npu-smi" in err


def test_parse_empty():
    cards, err = parse_npu_smi_info("")
    assert cards == [] and err == ""


def test_parse_no_cards():
    """设备表存在但无卡片行：返回空列表（detect_npu_groups 据此报"无设备"）。"""
    out = """+--------+---------------+
| NPU ID | Name          |
+========+===============+
+--------+---------------+
"""
    cards, err = parse_npu_smi_info(out)
    assert cards == [] and err == ""


def test_parse_missing_second_row():
    """只有第一行（无 Bus-Id 补充行）：字段降级为默认值，不报错。"""
    out = """| NPU ID | Name        | Health | Power(W) Temp(C) |
| 3      | Ascend910B  | OK     | 100.0    40        |
"""
    cards, _ = parse_npu_smi_info(out)
    assert len(cards) == 1
    c = cards[0]
    assert c["index"] == 3
    assert c["soc_name"] == "Ascend910B"
    assert c["health"] == "OK"
    assert c["power_w"] == 100.0
    assert c["temp_c"] == 40
    assert c["util_pct"] == 0
    assert c["mem_g"] == 0
    assert c["bus_id"] == ""


def test_parse_hbm_only_pair():
    """第二行仅 HBM used/total（无 Memory-Usage 列）也按最后一对解析。"""
    out = """| NPU ID | Name        | Health | Power(W) Temp(C)     |
|        |             | Bus-Id | Util(%)  HBM(MB)        |
| 0      | Ascend950PR | OK     | 200.0    55             |
|        |             | 0000:71:00.0 | 50 8824/131072  |
"""
    cards, _ = parse_npu_smi_info(out)
    assert cards[0]["util_pct"] == 50
    assert cards[0]["hbm_used_mb"] == 8824
    assert cards[0]["hbm_total_mb"] == 131072
    assert cards[0]["mem_g"] == 128


def test_parse_critical_health_preserved():
    """Health 原样保留（前端据此做 Warning/Alarm/Critical 着色）。"""
    out = """| NPU ID | Name        | Health     |
| 0      | Ascend950PR | Critical   |
| 1      | Ascend950PR | Warning    |
"""
    cards, _ = parse_npu_smi_info(out)
    assert [c["health"] for c in cards] == ["Critical", "Warning"]
