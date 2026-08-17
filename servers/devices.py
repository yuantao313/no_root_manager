"""目标机器基础设备信息采集：统一入口、TTL 缓存与快照回退。"""

from django.core.cache import cache

from .ssh import run_script

_CACHE_TIMEOUT = 1800
_DEVICE_CACHE_KEY = "nrm:device-info:{}"


def _host_script():
    from .scripts import HOST_INFO_SCRIPT

    return HOST_INFO_SCRIPT


def _parse_host(out: str) -> tuple[str, str, str]:
    """解析 host_info.sh 输出，返回 CPU、内存和根分区信息。"""
    values = {}
    for line in (out or "").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator:
            values[key] = value.strip()

    unknown = {"", "未知"}
    vendor = values.get("CPU_VENDOR", "")
    model = values.get("CPU_MODEL", "")
    parts = [value for value in (vendor if vendor != "GenuineIntel" else "", model) if value not in unknown]
    try:
        frequency = float(values.get("CPU_FREQ_MHZ", "") or 0)
    except ValueError:
        frequency = 0.0
    if frequency > 0:
        parts.append(f"@{frequency / 1000:.1f}GHz")
    cpu = " ".join(parts)
    cores = values.get("CPU_CORES", "")
    if cores not in unknown:
        cpu = f"{cpu} {cores}核" if cpu else f"{cores}核"
    return cpu, values.get("MEM_TOTAL", ""), values.get("DISK_ROOT", "")


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
    """返回基础设备信息，成功缓存 30 分钟，失败时回退数据库快照。"""
    from .models import Server

    key = _DEVICE_CACHE_KEY.format(server.pk)
    cached = cache.get(key)
    if cached is not None:
        return cached
    fresh = Server.objects.get(pk=server.pk)
    info = _collect_device_info(fresh)
    if info.get("msg"):
        return _fallback_to_snapshot(fresh, info)
    _save_snapshot(fresh, info)
    cache.set(key, info, _CACHE_TIMEOUT)
    return info


def clear_device_info_cache(server):
    """清空指定服务器的设备信息缓存。"""
    cache.delete(_DEVICE_CACHE_KEY.format(server.pk))
