# -*- coding: utf-8 -*-
"""从 ComfyUI 终端/日志解析 tqdm 进度（如 2/6 [02:53<05:47, 86.83s/it]）。"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 33%|█ | 2/6 [02:53<05:47, 86.83s/it]
_TQDM_RE = re.compile(
    r"(?:(?P<pct>\d{1,3})%\s*\|[^|\n]*\|\s*)?"
    r"(?P<cur>\d+)\s*/\s*(?P<tot>\d+)\s*"
    r"\[\s*(?P<elapsed>[0-9:]+)\s*<\s*(?P<remain>[0-9:]+)"
    r"(?:\s*,\s*(?P<rate>[\d.]+)\s*s/it)?\s*\]",
    re.I,
)

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"t": 0.0, "info": None}


def _clock_to_seconds(text: str) -> Optional[float]:
    parts = [p for p in (text or "").strip().split(":") if p != ""]
    if not parts:
        return None
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        return float(nums[0])
    if len(nums) == 2:
        return float(nums[0] * 60 + nums[1])
    if len(nums) == 3:
        return float(nums[0] * 3600 + nums[1] * 60 + nums[2])
    return None


def parse_tqdm_progress(text: str) -> Optional[Dict[str, Any]]:
    """从一段日志文本中取最后一条 tqdm 进度。"""
    if not text:
        return None
    last: Optional[re.Match[str]] = None
    for m in _TQDM_RE.finditer(text):
        last = m
    if not last:
        return None
    cur = int(last.group("cur"))
    tot = int(last.group("tot"))
    if tot <= 0:
        return None
    remain_s = _clock_to_seconds(last.group("remain") or "")
    elapsed_s = _clock_to_seconds(last.group("elapsed") or "")
    pct_g = last.group("pct")
    pct = int(pct_g) if pct_g is not None else int(100 * cur / tot)
    rate = None
    if last.group("rate"):
        try:
            rate = float(last.group("rate"))
        except ValueError:
            rate = None
    return {
        "step": cur,
        "steps": tot,
        "step_pct": max(0, min(100, pct)),
        "eta_s": remain_s,
        "elapsed_s": elapsed_s,
        "rate_s_it": rate,
        "raw_remain": (last.group("remain") or "").strip(),
        "raw_elapsed": (last.group("elapsed") or "").strip(),
        "source": "comfy_log",
    }


def _local_log_paths() -> List[Path]:
    paths: List[Path] = []
    env = (os.environ.get("COMFY_LOG_PATH") or "").strip()
    if env:
        paths.append(Path(env))
    root = (os.environ.get("COMFYUI_ROOT") or "").strip()
    if root:
        paths.append(Path(root) / "comfy_run.log")
    paths.append(Path("/home/linux/ComfyUI/comfy_run.log"))
    out: List[Path] = []
    seen = set()
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _read_local_tail(n: int = 120) -> str:
    for p in _local_log_paths():
        try:
            if not p.is_file():
                continue
            # 大日志只读尾部
            size = p.stat().st_size
            with p.open("rb") as f:
                if size > 64_000:
                    f.seek(max(0, size - 64_000))
                data = f.read().decode("utf-8", "replace")
            lines = data.splitlines()[-n:]
            return "\n".join(lines)
        except OSError:
            continue
    return ""


def _ssh_enabled() -> bool:
    flag = (os.environ.get("COMFY_LOG_SSH") or "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _read_ssh_tail(n: int = 120) -> str:
    if not _ssh_enabled():
        return ""
    host = (os.environ.get("COMFY_LOG_SSH_HOST") or "175.155.64.171").strip()
    port = (os.environ.get("COMFY_LOG_SSH_PORT") or "22128").strip()
    user = (os.environ.get("COMFY_LOG_SSH_USER") or "linux").strip()
    password = (os.environ.get("COMFY_LOG_SSH_PASSWORD") or "").strip()
    remote = (
        os.environ.get("COMFY_LOG_PATH") or "/home/linux/ComfyUI/comfy_run.log"
    ).strip()
    # 优先 sshpass（预览机已装）；失败再试 BatchMode 密钥
    remote_cmd = f"tail -n {int(n)} {remote}"
    tries: List[List[str]] = []
    if password and Path("/usr/bin/sshpass").is_file():
        tries.append(
            [
                "sshpass",
                "-p",
                password,
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=6",
                "-o",
                "BatchMode=no",
                "-p",
                port,
                f"{user}@{host}",
                remote_cmd,
            ]
        )
    tries.append(
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=6",
            "-o",
            "BatchMode=yes",
            "-p",
            port,
            f"{user}@{host}",
            remote_cmd,
        ]
    )
    for cmd in tries:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            if proc.returncode == 0 and (proc.stdout or "").strip():
                return proc.stdout
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def fetch_comfy_log_progress(*, force: bool = False) -> Optional[Dict[str, Any]]:
    """读取 Comfy 日志最新 tqdm；带短缓存，避免每个 poll 都 SSH。"""
    ttl = float(os.environ.get("COMFY_LOG_ETA_CACHE_SEC", "5") or 5)
    now = time.time()
    with _cache_lock:
        if (
            not force
            and _cache.get("info") is not None
            and now - float(_cache.get("t") or 0) < ttl
        ):
            return dict(_cache["info"]) if isinstance(_cache["info"], dict) else None

    text = _read_local_tail(140)
    if not text or not parse_tqdm_progress(text):
        text = _read_ssh_tail(140) or text
    info = parse_tqdm_progress(text)
    with _cache_lock:
        _cache["t"] = now
        _cache["info"] = dict(info) if info else None
    return dict(info) if info else None
