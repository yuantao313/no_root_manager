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


# ===== npu-smi 26.x 布局（216 真实输出：NPU ID 与型号同格、次行 Chip 号、AICore 列）=====
_NPU_OUT_26 = """+------------------------------------------------------------------------------------------------------------------+
| npu-smi 26.1.0                              Version: 26.1.0                                                      |
+---------------------------+---------------+----------------------------------------------------------------------+
| NPU   Name                | Health        | Power(W)             Temp(C)                 Hugepages-Usage(page)   |
| Chip                      | Bus-Id        | AICore(%)            Memory-Usage(MB)        HBM-Usage(MB)           |
+===========================+===============+======================================================================+
| 0     910B3               | OK            | 100.0                50                      0    / 0                |
| 0                         | 0000:C1:00.0  | 0                    0    / 0                8676 / 65536            |
+===========================+===============+======================================================================+
| 1     910B3               | OK            | 94.8                 48                      0    / 0                |
| 0                         | 0000:C2:00.0  | 0                    0    / 0                3403 / 65536            |
+===========================+===============+======================================================================+
| 2     910B3               | OK            | 90.0                 49                      0    / 0                |
| 0                         | 0000:81:00.0  | 0                    0    / 0                3403 / 65536            |
+===========================+===============+======================================================================+
| 3     910B3               | OK            | 96.5                 48                      0    / 0                |
| 0                         | 0000:82:00.0  | 0                    0    / 0                3407 / 65536            |
+===========================+===============+======================================================================+
| 4     910B3               | OK            | 96.5                 52                      0    / 0                |
| 0                         | 0000:01:00.0  | 0                    0    / 0                3403 / 65536            |
+===========================+===============+======================================================================+
| 5     910B3               | OK            | 97.4                 49                      0    / 0                |
| 0                         | 0000:02:00.0  | 0                    0    / 0                3402 / 65536            |
+===========================+===============+======================================================================+
| 6     910B3               | OK            | 97.8                 49                      0    / 0                |
| 0                         | 0000:41:00.0  | 0                    0    / 0                3402 / 65536            |
+===========================+===============+======================================================================+
| 7     910B3               | OK            | 93.3                 51                      0    / 0                |
| 0                         | 0000:42:00.0  | 0                    0    / 0                3403 / 65536            |
+===========================+===============+======================================================================+
+---------------------------+---------------+----------------------------------------------------------------------+
| NPU     Chip              | Process id    | Process name       | Process memory(MB)    | Process id in container |
+===========================+===============+======================================================================+
| 0       0                 | 485946        | pytest             | 5175                  | NA                      |
+===========================+===============+======================================================================+
| No running processes found in NPU 1                                                                              |
+===========================+===============+======================================================================+
| No running processes found in NPU 2                                                                              |
+===========================+===============+======================================================================+
| No running processes found in NPU 3                                                                              |
+===========================+===============+======================================================================+
| No running processes found in NPU 4                                                                              |
+===========================+===============+======================================================================+
| No running processes found in NPU 5                                                                              |
+===========================+===============+======================================================================+
| No running processes found in NPU 6                                                                              |
+===========================+===============+======================================================================+
| No running processes found in NPU 7                                                                              |
+===========================+===============+======================================================================+
"""


def test_parse_26x_layout_all_cards():
    """26.x 布局：8 张卡全部解析（修复旧解析器只剩 1 张卡的问题）。

    旧解析器按 cells[0].isdigit() 判断主行：26.x 主行 ``0     910B3`` 不是纯数字
    被跳过、次行 Chip 号 ``0`` 被误判为新卡，8 张卡 index 全是 0 互相覆盖，
    页面只剩一张卡。新解析器以 Bus-Id 优先识别次行、首格数字开头识别主行。
    """
    cards, err = parse_npu_smi_info(_NPU_OUT_26)
    assert err == ""
    assert [c["index"] for c in cards] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert all(c["soc_name"] == "910B3" for c in cards)
    assert all(c["health"] == "OK" for c in cards)
    # HBM 65536 MB → 64G
    assert all(c["mem_g"] == 64 for c in cards)
    assert all(c["hbm_total_mb"] == 65536 for c in cards)
    assert cards[0]["bus_id"] == "0000:C1:00.0"
    assert cards[0]["hbm_used_mb"] == 8676
    assert cards[0]["util_pct"] == 0
    assert cards[7]["bus_id"] == "0000:42:00.0"
    assert cards[7]["hbm_used_mb"] == 3403


def test_parse_26x_ignores_process_rows():
    """26.x 进程表行（首列 ``0       0`` 也是数字）与 No running processes 行不误解析。"""
    cards, _ = parse_npu_smi_info(_NPU_OUT_26)
    assert len(cards) == 8
    assert all(c["soc_name"] == "910B3" for c in cards)
