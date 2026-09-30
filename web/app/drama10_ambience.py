# -*- coding: utf-8
"""镜头表「环境声」→ 增强分层氛围轨。"""
from __future__ import annotations

from pathlib import Path

from .drama10_sfx import write_enhanced_ambience_wav

SR = 44100


def write_ambience_wav(path: Path, description: str, duration_sec: float, *, seed: int = 42) -> None:
    write_enhanced_ambience_wav(path, description, duration_sec, seed=seed)
