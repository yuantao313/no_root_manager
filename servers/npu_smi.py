"""npu-smi info 输出解析：目标机原始表格文本 → 结构化卡片信息。

采集链路：servers/scripts/npu_info.sh 在目标机执行 ``npu-smi info`` 并把
原始表格文本原样带回，本模块负责解析，供两处复用：

- ``servers/devices.py``：设备信息（NPU 卡型号/内存/健康状态）
- ``servers/management.py``：detect_npu_groups 卡组检测（NPU ID → npuN 组）

npu-smi info 输出格式（每张卡两行，之后是进程表，本模块只解析设备表）::

    | NPU ID | Name ...    | Health | Power(W)  Temp(C)  Hugepages-Usage(page) |
    |        | Bus-Id      | NPU Util(%) Memory-Usage(MB) HBM-Usage(MB)     |
    | 0      | Ascend950PR | OK     | 276.0     69       0     / 0            |
    |        | 0000:71:00.0| 99     0    / 0      8824  / 131072           |

不同版本列名可能微调，解析按位置 + ``used / total`` 模式提取，缺失字段降级为默认值。
"""

import re

# PCI Bus-Id 形如 0000:71:00.0（区分"第二行"，避免与表头/其他行混淆）
_BUS_ID_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$")
# "used / total" 内存对（Memory-Usage / HBM-Usage 均为该形式）
_PAIR_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def _split_row(line: str) -> list[str]:
    """把 ``| a | b |`` 行切成去空格单元格列表。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _parse_card_main(cells: list[str]) -> dict | None:
    """解析卡的第一行：NPU ID / Name / Health / Power Temp Hugepages。"""
    card = {
        "index": 0,
        "soc_name": "",
        "health": "",
        "power_w": 0.0,
        "temp_c": 0,
        "util_pct": 0,
        "hbm_used_mb": 0,
        "hbm_total_mb": 0,
        "mem_g": 0,
        "bus_id": "",
    }
    try:
        card["index"] = int(cells[0])
    except (ValueError, IndexError):
        return None
    card["soc_name"] = cells[1] if len(cells) > 1 else ""
    card["health"] = cells[2] if len(cells) > 2 else ""
    if len(cells) > 3:
        vals = cells[3].split()
        # 期望：<Power(W)> <Temp(C)> <hugepages used/total>，按位置容错取数
        if len(vals) >= 1:
            try:
                card["power_w"] = float(vals[0])
            except ValueError:
                pass
        if len(vals) >= 2:
            try:
                card["temp_c"] = int(vals[1])
            except ValueError:
                pass
    return card


def _merge_card_second(card: dict, cells: list[str], bus_col: int) -> None:
    """合并卡的第二行：Bus-Id / NPU Util(%) / Memory-Usage / HBM-Usage。"""
    card["bus_id"] = cells[bus_col]
    if len(cells) > bus_col + 1:
        vals = cells[bus_col + 1].split()
        if vals and vals[0].isdigit():
            try:
                card["util_pct"] = int(vals[0])
            except ValueError:
                pass
        # used/total 对：HBM-Usage 在最后一列，取最后一对作为 HBM 总内存
        pairs = _PAIR_RE.findall(cells[bus_col + 1])
        if pairs:
            used, total = int(pairs[-1][0]), int(pairs[-1][1])
            card["hbm_used_mb"] = used
            card["hbm_total_mb"] = total
            card["mem_g"] = total // 1024 if total else 0


def parse_npu_smi_info(output: str) -> tuple[list[dict], str]:
    """解析 ``npu-smi info`` 输出 → (cards, error)。

    - cards：每张卡一个 dict，含 index / soc_name / mem_g / health / power_w /
      temp_c / util_pct / hbm_used_mb / hbm_total_mb / bus_id
      （mem_g 为 HBM 总内存 MB→GB，131072 MB = 128G）
    - error：脚本输出的 ``NPU_ERROR=`` 内容（npu-smi 不可用/无输出等），无则空串
    """
    cards: list[dict] = []
    error = ""
    current: dict | None = None
    in_process_table = False
    for raw in (output or "").splitlines():
        line = raw.strip()
        if line.startswith("NPU_ERROR="):
            error = line.split("=", 1)[1].strip()
            continue
        if not line.startswith("|"):
            continue
        # 进程表表头出现后设备表解析结束（进程行首列也是数字，不能当卡片解析）
        if "Process id" in line or "Process name" in line:
            in_process_table = True
            continue
        if in_process_table:
            continue
        cells = _split_row(line)
        if not cells:
            continue
        if cells[0].isdigit():
            # 第一行：NPU ID 为数字 → 新卡
            current = _parse_card_main(cells)
            if current is not None:
                cards.append(current)
        elif current is not None:
            # 第二行：整行定位 Bus-Id 列（列前可能有空单元格，版本间列数微调），
            # 找到则作为当前卡的补充信息（利用率 / 内存）
            bus_col = next((i for i, c in enumerate(cells) if _BUS_ID_RE.match(c)), None)
            if bus_col is not None:
                _merge_card_second(current, cells, bus_col)
    return cards, error
