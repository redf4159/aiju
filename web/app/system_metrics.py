# -*- coding: utf-8 -*-
"""Host CPU / RAM / disk / GPU metrics for studio dashboard."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _read_proc_stat_cpu() -> tuple[int, int]:
    with open("/proc/stat", encoding="utf-8") as f:
        line = f.readline()
    parts = line.split()
    if len(parts) < 8 or parts[0] != "cpu":
        return 0, 0
    nums = [int(x) for x in parts[1:8]]
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    total = sum(nums)
    return total, idle


def _cpu_usage_percent(sample_sec: float = 0.25) -> float:
    try:
        t0, i0 = _read_proc_stat_cpu()
        time.sleep(max(0.05, sample_sec))
        t1, i1 = _read_proc_stat_cpu()
        dt = t1 - t0
        di = i1 - i0
        if dt <= 0:
            return 0.0
        return max(0.0, min(100.0, (1.0 - di / dt) * 100.0))
    except OSError:
        return 0.0


def _meminfo() -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                num = v.strip().split()[0]
                if num.isdigit():
                    out[k] = int(num)  # kB
    except OSError:
        pass
    return out


def _load_avg() -> List[float]:
    try:
        la = open("/proc/loadavg", encoding="utf-8").read().split()[:3]
        return [float(x) for x in la]
    except (OSError, ValueError):
        return [0.0, 0.0, 0.0]


def _cpu_count() -> int:
    try:
        return os.cpu_count() or 1
    except (TypeError, ValueError):
        return 1


def _disk_mount(path: Path, label: str) -> Dict[str, Any]:
    try:
        u = shutil.disk_usage(path)
        pct = (u.used / u.total * 100.0) if u.total else 0.0
        return {
            "label": label,
            "path": str(path),
            "total_gb": round(u.total / (1024**3), 2),
            "used_gb": round(u.used / (1024**3), 2),
            "free_gb": round(u.free / (1024**3), 2),
            "used_percent": round(pct, 1),
        }
    except OSError:
        return {"label": label, "path": str(path), "error": "不可用"}


def _gpu_metrics() -> List[Dict[str, Any]]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=8, text=True)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []
    gpus: List[Dict[str, Any]] = []
    for line in raw.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            mem_t = float(parts[2])
            mem_u = float(parts[3])
            mem_f = float(parts[4])
            util = float(parts[5])
            temp = float(parts[6])
        except ValueError:
            continue
        pct = (mem_u / mem_t * 100.0) if mem_t > 0 else 0.0
        gpus.append(
            {
                "index": int(parts[0]),
                "name": parts[1],
                "memory_total_mb": mem_t,
                "memory_used_mb": mem_u,
                "memory_free_mb": mem_f,
                "memory_used_percent": round(pct, 1),
                "utilization_gpu": util,
                "temperature_c": temp,
            }
        )
    return gpus


def collect_system_metrics() -> Dict[str, Any]:
    mi = _meminfo()
    mem_total = mi.get("MemTotal", 0)
    mem_avail = mi.get("MemAvailable", mi.get("MemFree", 0))
    mem_used = max(0, mem_total - mem_avail)
    swap_total = mi.get("SwapTotal", 0)
    swap_free = mi.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)

    data_root = Path(os.environ.get("BINGJING_DATA", "/home/linux/bingjing/web/data"))
    comfy_root = Path(os.environ.get("COMFYUI_ROOT", "/home/linux/ComfyUI"))

    disks = [
        _disk_mount(Path("/"), "系统盘 /"),
        _disk_mount(data_root, "项目数据"),
    ]
    if comfy_root.resolve() != Path("/").resolve():
        disks.append(_disk_mount(comfy_root, "ComfyUI"))

    cpus = _cpu_count()
    load = _load_avg()

    return {
        "ts": time.time(),
        "cpu": {
            "usage_percent": round(_cpu_usage_percent(0.22), 1),
            "cores": cpus,
            "load_1m": load[0],
            "load_5m": load[1],
            "load_15m": load[2],
            "load_per_core_1m": round(load[0] / cpus, 2) if cpus else load[0],
        },
        "memory": {
            "total_gb": round(mem_total / (1024 * 1024), 2),
            "used_gb": round(mem_used / (1024 * 1024), 2),
            "available_gb": round(mem_avail / (1024 * 1024), 2),
            "used_percent": round((mem_used / mem_total * 100.0) if mem_total else 0, 1),
        },
        "swap": {
            "total_gb": round(swap_total / (1024 * 1024), 2),
            "used_gb": round(swap_used / (1024 * 1024), 2),
            "used_percent": round((swap_used / swap_total * 100.0) if swap_total else 0, 1),
        },
        "disks": disks,
        "gpus": _gpu_metrics(),
    }
