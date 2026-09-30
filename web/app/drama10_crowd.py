# -*- coding: utf-8
"""群众对白：多音色 TTS 叠层 + 低语床。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional

from .drama10_sfx import write_crowd_murmur_wav

CROWD_ROLES = frozenset(
    {
        "大臣们",
        "群臣",
        "群众",
        "万民",
        "众人",
        "百姓",
        "朝臣",
    }
)


def is_crowd_role(role: str) -> bool:
    r = (role or "").strip()
    if r in CROWD_ROLES:
        return True
    return any(x in r for x in ("大臣", "群臣", "群众", "万民"))


def _crowd_voices() -> List[str]:
    raw = (
        os.environ.get("DRAMA10_CROWD_VOICES") or "echo,fable,ash,nova,onyx"
    ).strip()
    out = [v.strip().lower() for v in raw.split(",") if v.strip()]
    return out[:6] or ["echo", "fable", "ash", "nova"]


def _crowd_stagger_ms(index: int, base_ms: int) -> int:
    stagger = (0, 0, 55, 110, 165, 220)
    return base_ms + (stagger[index] if index < len(stagger) else index * 50)


def _crowd_volume(index: int) -> float:
    vols = (1.0, 0.88, 0.82, 0.76, 0.68, 0.62)
    return vols[index] if index < len(vols) else 0.6


def _crowd_atempo(index: int) -> float:
    tempos = (1.03, 0.97, 1.05, 1.0, 0.96, 1.02)
    return tempos[index] if index < len(tempos) else 1.0


def render_crowd_speech_wav(
    project_id: str,
    text: str,
    out_wav: Path,
    *,
    start_sec: float = 0.0,
    work_dir: Path,
    duration_hint: float = 6.0,
) -> None:
    from .character_ohmygpt import generate_character_voice_file

    content = (text or "").strip()
    if len(content) < 1:
        raise ValueError("群众台词为空")
    work_dir.mkdir(parents=True, exist_ok=True)
    voices = _crowd_voices()
    layers: List[Path] = []
    base_ms = max(0, int(float(start_sec) * 1000))

    for i, voice in enumerate(voices):
        mp3 = work_dir / f"crowd_{i}.mp3"
        generate_character_voice_file(mp3, text=content, voice=voice)
        delay = _crowd_stagger_ms(i, base_ms)
        vol = _crowd_volume(i)
        tempo = _crowd_atempo(i)
        wav = work_dir / f"crowd_{i}.wav"
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mp3),
                "-af",
                f"adelay={delay}|{delay},atempo={tempo},volume={vol},"
                f"highpass=f=100,lowpass=12000",
                str(wav),
            ],
            timeout=90,
        )
        layers.append(wav)

    murmur = work_dir / "crowd_murmur.wav"
    write_crowd_murmur_wav(murmur, duration_hint + 1.0, seed=hash(content) % 10000)
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(murmur),
            "-af",
            f"adelay={base_ms}|{base_ms},volume=0.42",
            str(work_dir / "crowd_murmur_d.wav"),
        ],
        timeout=60,
    )
    layers.append(work_dir / "crowd_murmur_d.wav")

    inputs: List[str] = []
    for p in layers:
        inputs.extend(["-i", str(p)])
    n = len(layers)
    filt = (
        "".join(f"[{i}:a]" for i in range(n))
        + f"amix=inputs={n}:duration=longest:dropout_transition=0[aout]"
    )
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            *inputs,
            "-filter_complex",
            filt,
            "-map",
            "[aout]",
            str(out_wav),
        ],
        timeout=180,
    )
