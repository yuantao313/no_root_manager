"""目标机器基础设备信息采集：统一入口、TTL 缓存与快照回退。"""

import time

from .ssh import run_script

_DEVICE_CACHE: dict[int, tuple[float, dict]] = {}
_SUCCESS_TTL = 1800.0


def _host_script():
    from .scripts import HOST_INFO_SCRIPT

    return HOST_INFO_SCRIPT


def _parse_host(out: str) -> tuple[str, str, str]:
    """解析 host_info.sh 输出，返回 CPU、内存和根分区信息。"""
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
        parts.append(cpu_vendor)
    if cpu_model and cpu_model not in ("未知", ""):
        parts.append(cpu_model)
    if cpu_freq > 0:
        parts.append(f"@{cpu_freq / 1000:.1f}GHz")
    cpu = " ".join(parts)
    if cpu_cores and cpu_cores not in ("未知", ""):
        cpu = f"{cpu} {cpu_cores}核" if cpu else f"{cpu_cores}核"
    return cpu, memory, disk


def _collect_device_info(server) -> dict:
    """无缓存地采集 CPU、内存和根分区信息。"""
    ok, out, err = run_script(server, _host_script(), timeout=20, connect_timeout=5)
    if not ok:
        return {"cpu": "", "memory": "", "disk": "", "msg": err or "主机信息采集失败"}
    cpu, memory, disk = _parse_host(out)
    return {"cpu": cpu, "memory": memory, "disk": disk, "msg": ""}


def _save_snapshot(server, info: dict) -> None:
    server.__class__.objects.filter(pk=server.pk).update(
        device_info_snapshot={
            "cpu": info.get("cpu", ""),
            "memory": info.get("memory", ""),
            "disk": info.get("disk", ""),
        }
    )


def _fallback_to_snapshot(server, info: dict) -> dict:
    snapshot = server.device_info_snapshot or {}
    if not any(snapshot.get(key) for key in ("cpu", "memory", "disk")):
        return info
    return {
        "cpu": snapshot.get("cpu", ""),
        "memory": snapshot.get("memory", ""),
        "disk": snapshot.get("disk", ""),
        "msg": (info.get("msg") or "设备信息获取失败") + "（显示最近一次成功采集的信息）",
    }


def get_device_info(server) -> dict:
    """返回基础设备信息，成功缓存 30 分钟，失败短暂缓存并回退快照。"""
    from .models import Server

    now = time.monotonic()
    cached = _DEVICE_CACHE.get(server.pk)
    if cached is not None:
        ts, info = cached
        if now - ts < _SUCCESS_TTL:
            return info
    fresh = Server.objects.get(pk=server.pk)
    info = _collect_device_info(fresh)
    if info.get("msg"):
        return _fallback_to_snapshot(fresh, info)
    _save_snapshot(fresh, info)
    _DEVICE_CACHE[server.pk] = (now, info)
    return info


def clear_device_info_cache():
    """清空设备信息缓存。"""
    _DEVICE_CACHE.clear()
