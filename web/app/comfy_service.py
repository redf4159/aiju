# -*- coding: utf-8 -*-
"""ComfyUI systemd 状态与控制（制作台同机 user 单元）。"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional

from .comfy_client import ComfyClient

COMFY_UNIT = os.environ.get("COMFY_SYSTEMD_UNIT", "bingjing-comfy.service")


def _run_systemctl(args: List[str], timeout: float = 90.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _systemd_is_active() -> str:
    try:
        r = _run_systemctl(["is-active", COMFY_UNIT], timeout=15.0)
        return (r.stdout or r.stderr or "unknown").strip() or "unknown"
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"


def _systemd_props() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        r = _run_systemctl(
            ["show", COMFY_UNIT, "--property=ActiveState,SubState,MainPID,Result"],
            timeout=15.0,
        )
        for line in (r.stdout or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    except (subprocess.TimeoutExpired, OSError):
        pass
    return out


def _queue_counts(queue: Dict[str, Any]) -> tuple[int, int]:
    running = queue.get("queue_running") or []
    pending = queue.get("queue_pending") or []
    return len(running), len(pending)


def collect_comfy_status() -> Dict[str, Any]:
    try:
        from .studio_settings import (
            get_video_backend_settings_public,
            use_ppio_video,
            use_minimax_official,
            active_video_backend,
        )

        vb = get_video_backend_settings_public()
        using_ppio = use_ppio_video()
        using_minimax = use_minimax_official()
        active_be = active_video_backend()
    except Exception:
        vb = {}
        using_ppio = False
        using_minimax = False
        active_be = "comfy"

    if using_minimax:
        active = "active"
        props = {"ActiveState": "active", "SubState": "minimax-official"}
    elif using_ppio:
        active = "active"
        props = {"ActiveState": "active", "SubState": "ppio-async"}
    else:
        active = _systemd_is_active()
        props = _systemd_props()
    http_ok = False
    http_error: Optional[str] = None
    queue_running = 0
    queue_pending = 0
    if using_minimax:
        http_ok = True
    elif using_ppio or active == "active":
        try:
            client = ComfyClient()
            client.ping()
            http_ok = True
            q = client.get_queue()
            queue_running, queue_pending = _queue_counts(q)
        except Exception as e:
            http_error = str(e)
    summary = _status_summary(active, http_ok, props)
    if using_minimax:
        mm = (vb.get("minimax") or {})
        summary = f"MiniMax 官方 · {mm.get('model') or 'H3'} · {mm.get('resolution') or ''}".strip()
    elif using_ppio:
        summary = "PPIO Async" + (" · 可达" if http_ok else " · 异常")
    return {
        "unit": COMFY_UNIT,
        "systemd_active": active,
        "active_state": props.get("ActiveState", active),
        "sub_state": props.get("SubState", ""),
        "main_pid": int(props.get("MainPID") or "0") or None,
        "http_ok": http_ok,
        "http_error": http_error,
        "queue_running": queue_running,
        "queue_pending": queue_pending,
        "summary": summary,
        "video_backend": vb,
        "comfyui_url": (
            f"minimax://{(vb.get('minimax') or {}).get('api_base')}"
            if active_be == "minimax"
            else (
                f"ppio://{(vb.get('ppio') or {}).get('endpoint_key')}"
                if using_ppio
                else os.environ.get("COMFYUI_URL", "http://127.0.0.1:8000")
            )
        ),
        "public_comfyui_url": os.environ.get(
            "PUBLIC_COMFYUI_URL", "http://127.0.0.1:8000"
        ),
    }


def _status_summary(active: str, http_ok: bool, props: Dict[str, str]) -> str:
    if active in ("inactive", "failed", "dead"):
        return "已停止"
    if active == "activating":
        return "启动中"
    if active == "active" and http_ok:
        return "运行中"
    if active == "active":
        return "异常（进程在但 API 无响应）"
    return active or "未知"


def _queue_busy() -> bool:
    try:
        q = ComfyClient().get_queue()
        r, p = _queue_counts(q)
        return r > 0 or p > 0
    except Exception:
        return False


def control_comfy(action: str, *, force: bool = False) -> Dict[str, Any]:
    if action not in ("start", "stop", "restart"):
        raise ValueError(f"unsupported action: {action}")
    if action in ("stop", "restart") and not force and _queue_busy():
        raise RuntimeError("ComfyUI 队列中仍有任务，请确认后再停止或重启")
    r = _run_systemctl([action, COMFY_UNIT])
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip() or f"systemctl {action} 失败"
        raise RuntimeError(msg)
    return collect_comfy_status()
