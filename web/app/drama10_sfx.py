# -*- coding: utf-8
"""环境声：仅自然事件（风 gust、烛爆、脚步、殿内混响），禁止连续粉噪/正弦 hum（听感像电流）。"""
from __future__ import annotations

import hashlib
import os
import subprocess
import wave
from pathlib import Path
from typing import List, Tuple

import numpy as np

SR = 44100
_PKG = Path(__file__).resolve().parent
SFX_CACHE = _PKG.parent / "data" / "sfx_cache"


def _rng(seed: int) -> np.random.RandomState:
    return np.random.RandomState(int(seed) & 0xFFFFFFFF)


def _gust_wind(n: int, seed: int, gain: float) -> np.ndarray:
    """短时风 gust，非连续嗡声。"""
    rng = _rng(seed)
    out = np.zeros(n, dtype=np.float64)
    pos = int(0.15 * SR)
    while pos < n - int(0.2 * SR):
        wlen = int(rng.uniform(0.25, 0.9) * SR)
        if pos + wlen >= n:
            break
        alpha = 0.04
        seg_n = min(wlen, n - pos)
        noise = rng.randn(seg_n)
        w = np.zeros(seg_n, dtype=np.float64)
        for i in range(1, seg_n):
            w[i] = w[i - 1] + alpha * (noise[i] - w[i - 1])
        env = np.hanning(seg_n)
        out[pos : pos + seg_n] += w * env * gain
        pos += seg_n + int(rng.uniform(0.4, 1.2) * SR)
    return out.astype(np.float32)


def _candle_crackle(n: int, seed: int) -> np.ndarray:
    rng = _rng(seed + 19)
    out = np.zeros(n, dtype=np.float64)
    for _ in range(max(8, int(n / SR * 3))):
        pos = rng.randint(0, max(1, n - 400))
        wlen = rng.randint(80, 220)
        env = np.hanning(min(wlen, n - pos))
        out[pos : pos + len(env)] += rng.randn(len(env)) * env * 0.35
    return out.astype(np.float32)


def _footstep_layer(n: int, seed: int) -> np.ndarray:
    rng = _rng(seed + 23)
    out = np.zeros(n, dtype=np.float64)
    step = int(0.55 * SR)
    for pos in range(int(0.3 * SR), n, step + rng.randint(-200, 200)):
        wlen = int(0.035 * SR)
        out[pos : min(n, pos + wlen)] += np.hanning(min(wlen, n - pos)) * 0.45
    return out.astype(np.float32)


def _murmur_layer(n: int, seed: int) -> np.ndarray:
    rng = _rng(seed + 41)
    alpha = 0.04
    noise = rng.randn(n)
    m = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        m[i] = m[i - 1] + alpha * (noise[i] - m[i - 1])
    return (m * 0.35).astype(np.float32)


def _keywords(desc: str) -> List[str]:
    d = (desc or "").strip().lower()
    tags: List[str] = []
    rules: List[Tuple[str, Tuple[str, ...]]] = [
        ("blizzard", ("暴风", "风雪", "暴雪", "雪", "snow", "snowstorm", "blizzard", "winter wind")),
        ("hall", (
            "大殿", "大厅", "殿", "混响", "空腔", "palace", "corridor", "hall",
            "chamber", "candle", "烛", "长廊", "王宫", "quiet palace",
        )),
        ("foot", ("脚步", "走", "footstep", "walking")),
        ("crowd", ("人群", "低语", "万民", "crowd", "murmur")),
    ]
    for tag, keys in rules:
        if any(k.lower() in d for k in keys):
            tags.append(tag)
    return tags or ["generic"]


def _write_stereo_wav(path: Path, left: np.ndarray, right: np.ndarray) -> None:
    n = min(len(left), len(right))
    left, right = left[:n], right[:n]
    peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))), 1e-6)
    scale = min(0.92, 0.75 / peak)
    left = (left * scale).astype(np.float32)
    right = (right * scale).astype(np.float32)
    fade = min(int(0.35 * SR), n // 4)
    if fade > 0:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        left[:fade] *= ramp
        left[-fade:] *= ramp[::-1]
        right[:fade] *= ramp
        right[-fade:] *= ramp[::-1]
    inter = np.empty(n * 2, dtype=np.int16)
    inter[0::2] = (left * 32767).astype(np.int16)
    inter[1::2] = (right * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(inter.tobytes())


def _ffmpeg_dehum(path: Path) -> None:
    """去掉 50–180Hz 电源 hum 带，保留语音与自然瞬态。"""
    tmp = path.with_suffix(".dehum.wav")
    af = os.environ.get("DRAMA10_DEHUM_AF") or "highpass=f=200,lowpass=11000"
    try:
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-af",
                af,
                str(tmp),
            ],
            timeout=120,
        )
        if tmp.is_file() and tmp.stat().st_size > 256:
            tmp.replace(path)
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)


def write_enhanced_ambience_wav(
    path: Path, description: str, duration_sec: float, *, seed: int = 42
) -> None:
    n = max(1, int(float(duration_sec) * SR))
    tags = _keywords(description)
    L = np.zeros(n, dtype=np.float32)
    R = np.zeros(n, dtype=np.float32)

    if "hall" in tags:
        h = _murmur_layer(n, seed) * 1.1
        cr = _candle_crackle(n, seed)
        L += h + cr * 0.9
        R += h * 1.05 + cr
    if "blizzard" in tags or "generic" in tags:
        g1 = _gust_wind(n, seed, 0.55)
        g2 = _gust_wind(n, seed + 9, 0.5)
        L += g1
        R += g2
    if "foot" in tags:
        fs = _footstep_layer(n, seed)
        L += fs * 0.85
        R += fs * 0.85
    if "crowd" in tags:
        mur = _murmur_layer(n, seed + 99) * 1.0
        L += mur
        R += mur * 0.95

    if np.max(np.abs(L)) < 1e-5:
        g = _gust_wind(n, seed, 0.45)
        L += g
        R += g

    cache_key = hashlib.md5(
        f"{description}|{duration_sec:.2f}|{seed}|natural_v6".encode("utf-8")
    ).hexdigest()[:16]
    SFX_CACHE.mkdir(parents=True, exist_ok=True)
    base = SFX_CACHE / f"amb_{cache_key}.wav"
    _write_stereo_wav(base, L, R)
    if "hall" in tags:
        subprocess_check_echo(base)
    _ffmpeg_dehum(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base.read_bytes())


def subprocess_check_echo(base: Path) -> None:
    tmp = base.with_suffix(".hall.wav")
    try:
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(base),
                "-af",
                "aecho=0.55:0.48:90:0.22",
                str(tmp),
            ],
            timeout=90,
        )
        if tmp.is_file() and tmp.stat().st_size > 256:
            tmp.replace(base)
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)


def write_bgm_bed_wav(path: Path, duration_sec: float, *, seed: int = 7) -> None:
    """已弃用连续 BGM 床（易听成电流）；写入极短静音占位，由环境层承担氛围。"""
    n = max(1, int(float(duration_sec) * SR))
    silence = np.zeros(n, dtype=np.float32)
    _write_stereo_wav(path, silence, silence)


def write_crowd_murmur_wav(path: Path, duration_sec: float, *, seed: int = 7) -> None:
    n = max(1, int(float(duration_sec) * SR))
    L = _murmur_layer(n, seed) * 1.2
    R = _murmur_layer(n, seed + 3) * 1.1
    _write_stereo_wav(path, L, R)
    _ffmpeg_dehum(path)
