# -*- coding: utf-8
"""Drama10 配音：优先 OpenAI 兼容 HTTP TTS（OhMyGPT），失败再试 edge-tts。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional


def narrator_api_voice() -> str:
    from .drama10_voice import resolve_api_voice

    return resolve_api_voice("旁白")


def synthesize_speech_file(
    dest: Path,
    text: str,
    role: str = "旁白",
    *,
    project_id: Optional[str] = None,
) -> None:
    content = (text or "").strip()
    if len(content) < 1:
        raise ValueError("TTS 文本为空")
    from .drama10_voice import resolve_api_voice

    api_voice = resolve_api_voice(role, project_id=project_id)
    err: list[str] = []
    try:
        from .character_ohmygpt import generate_character_voice_file

        generate_character_voice_file(dest, text=content, voice=api_voice)
        if dest.is_file() and dest.stat().st_size > 128:
            _apply_role_voice_fx(dest, role)
            return
    except Exception as e:
        err.append(f"HTTP TTS: {e}")

    async def _edge() -> None:
        import edge_tts

        if role in ("旁白", "OS", "os"):
            voice = os.environ.get("DRAMA10_NARRATOR_EDGE_VOICE") or "zh-CN-YunjianNeural"
            rate = os.environ.get("DRAMA10_NARRATOR_EDGE_RATE") or "-6%"
        else:
            voice = "zh-CN-YunxiNeural"
            rate = "+0%"
        comm = edge_tts.Communicate(content, voice, rate=rate)
        await comm.save(str(dest))

    try:
        asyncio.run(_edge())
        if dest.is_file() and dest.stat().st_size > 128:
            _apply_role_voice_fx(dest, role)
            return
    except Exception as e:
        err.append(f"edge-tts: {e}")

    raise RuntimeError("；".join(err) or "TTS 失败")


def _apply_role_voice_fx(dest: Path, role: str) -> None:
    from .drama10_voice import SHADOW_ROLES

    role = (role or "").strip()
    if role not in SHADOW_ROLES:
        return
    import subprocess

    tmp = dest.with_suffix(".fx.mp3")
    af = (
        os.environ.get("DRAMA10_SHADOW_AF")
        or "aecho=0.85:0.88:1200:0.45,lowpass=f=3200,volume=0.92"
    )
    try:
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(dest),
                "-af",
                af,
                str(tmp),
            ],
            timeout=60,
        )
        if tmp.is_file() and tmp.stat().st_size > 128:
            tmp.replace(dest)
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)
