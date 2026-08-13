"""npu-smi info 输出解析：目标机原始表格文本 → 结构化卡片信息。

采集链路：servers/scripts/npu_info.sh 在目标机执行 ``npu-smi info`` 并把
原始表格文本原样带回，本模块负责解析，供两处复用：

- ``servers/devices.py``：设备信息（NPU 卡型号/内存/健康状态）
- ``servers/management.py``：detect_npu_groups 卡组检测（NPU ID → npuN 组）

npu-smi info 输出格式因版本而异，实测两种布局（每张卡两行，之后是进程表，
本模块只解析设备表）::

    25.x：| NPU ID | Name       | Health | Power(W) Temp(C) Hugepages-Usage(page) |
          |        | Bus-Id     | NPU Util(%) Memory-Usage(MB) HBM-Usage(MB)     |
          | 0      | Ascend950PR| OK     | 276.0     69      0 / 0             |
          |        | 0000:71:00.0 | 99  0 / 0        8824 / 131072           |

    26.x：| NPU   Name      | Health | Power(W) Temp(C) Hugepages-Usage(page) |
          | Chip            | Bus-Id | AICore(%) Memory-Usage(MB) HBM-Usage(MB) |
          | 0     910B3     | OK     | 100.0     49      0 / 0             |
          | 0               | 0000:C1:00.0 | 0  0 / 0  8676 / 65536       |

差异点：26.x 的 NPU ID 与型号同格（``0     910B3``）、次行首列是 Chip 号
（数字，非空）、利用率列名为 AICore(%)。解析策略不依赖列名/列位置：

- **次行识别**：行内存在 PCI Bus-Id（``0000:xx:xx.x``）即视为次行
- **主行识别**：首格以数字开头（25.x 纯 ID / 26.x ID+型号同格）
- **Health 定位**：主行中按已知取值（OK/Warning/Alarm/Critical/Unknown）定位
  健康列，其前为型号（25.x 单独列），其后为 Power/Temp 数据
"""

import re

# PCI Bus-Id 形如 0000:71:00.0（用于识别"次行"，兼容版本间列数差异）
_BUS_ID_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$")
# "used / total" 内存对（Memory-Usage / HBM-Usage 均为该形式）
_PAIR_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
# npu-smi Health 列的已知取值（用于在主行中定位健康列，兼容 25.x/26.x 列位置差异）
_HEALTH_VALUES = {"OK", "Warning", "Alarm", "Critical", "Unknown"}


def _split_row(line: str) -> list[str]:
    """把 ``| a | b |`` 行切成去空格单元格列表。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _parse_card_main(cells: list[str]) -> dict | None:
    """解析卡的主行（NPU ID / 型号 / Health / Power Temp Hugepages）。

    兼容两种布局：
    - 25.x：``| ID | Name | Health | 数据 |``（ID 单独一格）
    - 26.x：``| ID Name | Health | 数据 |``（ID 与型号同格）

    通过定位 Health 取值列区分：Health 之前的列视为型号，之后的列解析功耗/温度。
    """
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
    first_tokens = cells[0].split()
    if not first_tokens or not first_tokens[0].isdigit():
        return None
    card["index"] = int(first_tokens[0])
    # 型号候选：26.x 布局下与 ID 同格（"0     910B3" → "910B3"）
    name_parts = first_tokens[1:]
    # 定位 Health 列（25.x 在 cells[2]，26.x 在 cells[1]）
    health_col = next((i for i, c in enumerate(cells[1:], start=1) if c in _HEALTH_VALUES), None)
    if health_col is not None:
        card["health"] = cells[health_col]
        # Health 之前的列是型号（25.x 布局的单独 Name 列）
        name_parts.extend(c for c in cells[1:health_col] if c)
        # Health 之后是 Power/Temp/Hugepages 数据
        if health_col + 1 < len(cells):
            vals = cells[health_col + 1].split()
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
    else:
        # 兜底：无 Health 列时，其余非 Bus-Id 列并入型号
        name_parts.extend(c for c in cells[1:] if c and not _BUS_ID_RE.match(c))
    card["soc_name"] = " ".join(p for p in name_parts if p).strip()
    return card


def _merge_card_second(card: dict, cells: list[str], bus_col: int) -> None:
    """合并卡的次行：Bus-Id / 利用率(%) / Memory-Usage / HBM-Usage。"""
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
        # 次行：行内含 PCI Bus-Id（25.x 首列为空 / 26.x 首列为 Chip 号）→ 补充当前卡
        bus_col = next((i for i, c in enumerate(cells) if _BUS_ID_RE.match(c)), None)
        if bus_col is not None:
            if current is not None:
                _merge_card_second(current, cells, bus_col)
            continue
        # 主行：首格以数字开头（25.x 纯 ID / 26.x "ID Name" 同格）→ 新卡
        first_tokens = cells[0].split()
        if first_tokens and first_tokens[0].isdigit():
            current = _parse_card_main(cells)
            if current is not None:
                cards.append(current)
    return cards, error
