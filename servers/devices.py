"""目标机设备信息查询：统一入口 + LRU 缓存。

所有涉及 NPU / CPU / 内存 / 硬盘的查询统一收敛到 ``get_device_info``，
函数按服务器 pk 做 LRU 缓存（进程内），避免每次访问都 SSH 探测真实机器。

执行方式：按设备类型上传并运行仓库内置独立脚本（root 一次性执行，脚本内部
不再逐条 sudo，避免非 root SSH + 未配置 NOPASSWD sudo 时全部查询失败）：

- ``host_info.sh``：CPU / 内存 / 硬盘（所有服务器都查）
- ``npu_info.sh``：NPU 卡型号与内存（仅 NPU 服务器；先 source CANN 环境再查 acl）
- ``gpu_info.sh``：GPU 卡信息（预留，暂不实现）

各脚本输出 key=value 行、命名空间隔离（CPU_MODEL vs NPU_CARD vs GPU_CARD），
本模块按脚本分别执行、分别解析、合并返回；任何单项失败不影响其他项。

- NPU：型号 ``acl.get_soc_name()``，内存 ``acl.rt.get_mem_info(dev)[1]``（字节→GB）
- GPU：预留结构，暂不实现
- CPU 型号优先 lscpu（鲲鹏 ARM 机 /proc/cpuinfo 无 model name）

返回结构（JSON 友好，可直接喂给 groups API / 页面展示）：
    {
        "npu": [{"index": 0, "soc_name": "Ascend 950 Pro", "mem_g": 128}, ...],
        "gpu": [],  # 预留：GPU 探测未实现（脚本输出命名空间与 NPU 隔离）
        "cpu": "Kunpeng 920 192核",
        "memory": "512G",
        "disk": "916G（已用 120G）",
        "msg": "查询说明/错误信息",
    }
"""

import time

from .ssh import run_script

# 设备信息缓存：server_pk -> (缓存时间戳, 结果)。
# - 成功结果缓存 10 分钟（避免每次访问都 SSH）
# - 失败结果（msg 非空）负缓存 60 秒：目标机不可达时页面不至于每次请求都卡 SSH 超时，
#   也不会像 lru_cache 那样把失败结果永久记住（CPU 空白/NPU 空不再"被缓存钉死"）
_DEVICE_CACHE: dict[int, tuple[float, dict]] = {}
_SUCCESS_TTL = 600.0  # 成功缓存 10 分钟
_FAIL_TTL = 60.0  # 失败负缓存 60 秒


def _scripts():
    from .scripts import GPU_INFO_SCRIPT, HOST_INFO_SCRIPT, NPU_INFO_SCRIPT

    return HOST_INFO_SCRIPT, NPU_INFO_SCRIPT, GPU_INFO_SCRIPT


def _parse_host(out: str) -> tuple[str, str, str]:
    """解析 host_info.sh 输出 → (cpu, memory, disk)。

    CPU 描述：<厂商> <型号> @<频率GHz>，<核数>核（字段缺失时逐段降级）。
    """
    cpu_vendor = ""
    cpu_model = ""
    cpu_freq = 0.0
    cpu_cores = ""
    memory = ""
    disk = ""
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("CPU_VENDOR="):
            cpu_vendor = line.split("=", 1)[1].strip()
        elif line.startswith("CPU_MODEL="):
            cpu_model = line.split("=", 1)[1].strip()
        elif line.startswith("CPU_FREQ_MHZ="):
            try:
                cpu_freq = float(line.split("=", 1)[1].strip() or 0)
            except ValueError:
                cpu_freq = 0.0
        elif line.startswith("CPU_CORES="):
            cpu_cores = line.split("=", 1)[1].strip()
        elif line.startswith("MEM_TOTAL="):
            memory = line.split("=", 1)[1].strip()
        elif line.startswith("DISK_ROOT="):
            disk = line.split("=", 1)[1].strip()
    parts = []
    if cpu_vendor and cpu_vendor not in ("未知", "GenuineIntel"):
        # GenuineIntel 冗余（型号已含 Intel(R)），只展示 AMD/HiSilicon 等差异厂商
        parts.append(cpu_vendor)
    if cpu_model and cpu_model not in ("未知", ""):
        parts.append(cpu_model)
    if cpu_freq > 0:
        parts.append(f"@{cpu_freq / 1000:.1f}GHz")
    cpu = " ".join(parts)
    if cpu_cores and cpu_cores not in ("未知", ""):
        cpu = f"{cpu} {cpu_cores}核" if cpu else f"{cpu_cores}核"
    return cpu, memory, disk


def _parse_cards(out: str, prefix: str) -> tuple[list[dict], str]:
    """解析 <PREFIX>_CARD <设备号> <内存GB> <型号…> 行 → (cards, error)。

    prefix 为 NPU 或 GPU；型号可能含空格，故用 maxsplit=3 切分。
    """
    cards: list[dict] = []
    error = ""
    tag = f"{prefix}_CARD "
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith(tag):
            parts = line.split(maxsplit=3)
            if len(parts) >= 3:
                try:
                    idx = int(parts[1])
                except ValueError:
                    continue
                try:
                    mem_g = int(parts[2])
                except ValueError:
                    mem_g = 0
                cards.append({"index": idx, "soc_name": parts[3] if len(parts) > 3 else "", "mem_g": mem_g})
        elif line.startswith(f"{prefix}_ERROR="):
            error = line.split("=", 1)[1].strip()
    return cards, error


def _collect_device_info(server) -> dict:
    """无缓存地采集设备信息：按类型上传执行脚本，分别解析后合并。"""
    host_script, npu_script, gpu_script = _scripts()

    cpu = memory = disk = ""
    npu: list[dict] = []
    gpu: list[dict] = []
    msg = ""

    # 主机信息：所有服务器都查（单项失败不影响其他）。
    # 页面渲染路径同步执行，用短连接/执行超时，避免目标机不可达时拖死页面
    ok, out, err = run_script(server, host_script, timeout=20, connect_timeout=5)
    if ok:
        cpu, memory, disk = _parse_host(out)
    else:
        msg = err or "主机信息采集失败"

    # NPU 卡：仅 NPU 服务器
    if server.is_npu:
        ok, out, err = run_script(server, npu_script, timeout=20, connect_timeout=5)
        if ok:
            npu, npu_err = _parse_cards(out, "NPU")
            msg = (msg + "；" if msg else "") + npu_err if npu_err else msg
        else:
            msg = (msg + "；" if msg else "") + (err or "NPU 信息采集失败")

    # GPU 卡：预留（当前脚本仅输出 GPU_ERROR 占位，不执行采集）
    # ok, out, err = run_script(server, gpu_script, timeout=60)
    # if ok:
    #     gpu, gpu_err = _parse_cards(out, "GPU")
    #     ...

    return {
        "npu": npu,
        "gpu": gpu,
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "msg": msg,
    }


def _save_snapshot(server, info: dict) -> None:
    """把成功采集的设备信息落库（Server.device_info_snapshot）。

    目标机不可达时回退展示用：保证页面不空白。
    """
    from django.utils import timezone

    Server = server.__class__
    Server.objects.filter(pk=server.pk).update(
        device_info_snapshot={
            "npu": info.get("npu", []),
            "gpu": info.get("gpu", []),
            "cpu": info.get("cpu", ""),
            "memory": info.get("memory", ""),
            "disk": info.get("disk", ""),
        },
        device_info_updated_at=timezone.now(),
    )


def _fallback_to_snapshot(server, info: dict) -> dict:
    """采集失败时回退数据库快照：有已存信息则展示，并注明来源。"""
    snapshot = server.device_info_snapshot or {}
    has_data = any([snapshot.get("cpu"), snapshot.get("memory"), snapshot.get("disk"), snapshot.get("npu")])
    if not has_data:
        return info
    fallback = {
        "npu": snapshot.get("npu", []),
        "gpu": snapshot.get("gpu", []),
        "cpu": snapshot.get("cpu", ""),
        "memory": snapshot.get("memory", ""),
        "disk": snapshot.get("disk", ""),
        # 注明是回退展示，避免用户误以为是最新实时数据
        "msg": (info.get("msg") or "设备信息获取失败") + "（显示最近一次成功采集的信息）",
    }
    return fallback


def get_device_info(server) -> dict:
    """统一入口：返回目标机设备信息（TTL 缓存 + 数据库快照回退）。

    - 成功结果缓存 10 分钟（避免每次访问都 SSH），并落库 Server.device_info_snapshot
    - 失败结果（msg 非空）负缓存 60 秒：目标机不可达时页面不至于每次请求都卡 SSH 超时；
      且回退展示数据库快照（最近一次成功采集），页面不空白
    GPU 预留：未来支持时在此启用 gpu_info.sh 分支。
    """
    from .models import Server

    now = time.monotonic()
    cached = _DEVICE_CACHE.get(server.pk)
    if cached is not None:
        ts, info = cached
        ttl = _SUCCESS_TTL if not info.get("msg") else _FAIL_TTL
        if now - ts < ttl:
            return info
    # 未命中或缓存过期：重新采集（失败结果也写入，但短 TTL 负缓存）
    fresh = Server.objects.get(pk=server.pk)
    info = _collect_device_info(fresh)
    _DEVICE_CACHE[server.pk] = (now, info)
    if info.get("msg"):
        # 采集失败：回退数据库快照（页面不空白），并尝试清理失败缓存（下次可重试）
        _DEVICE_CACHE.pop(server.pk, None)
        return _fallback_to_snapshot(fresh, info)
    # 采集成功：落库快照供下次失败回退
    _save_snapshot(fresh, info)
    return info


def clear_device_info_cache(server=None):
    """清空设备信息缓存（服务器修改后或详情页刷新时调用）。

    server 为空清全部，否则只清单台。
    """
    if server is None:
        _DEVICE_CACHE.clear()
    else:
        _DEVICE_CACHE.pop(server.pk, None)
