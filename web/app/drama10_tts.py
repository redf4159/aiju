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


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def synthesize_speech_file(
    dest: Path,
    text: str,
    role: str = "旁白",
    *,
    project_id: Optional[str] = None,
    voice: Optional[str] = None,
) -> None:
    content = (text or "").strip()
    if len(content) < 1:
        raise ValueError("TTS 文本为空")
    from .drama10_voice import (
        OPENAI_TTS_VOICES,
        infer_api_voice_from_gender_text,
        resolve_api_voice,
    )

    api_voice = (voice or "").strip().lower()
    if api_voice not in OPENAI_TTS_VOICES:
        api_voice = resolve_api_voice(role, project_id=project_id)
    err: list[str] = []
    gender_guess = infer_api_voice_from_gender_text(role)
    if project_id and not gender_guess:
        try:
            from . import services

            for ch in services.list_characters(project_id):
                if (ch.get("name") or "").strip() == (role or "").strip():
                    gender_guess = infer_api_voice_from_gender_text(
                        ch.get("voice_desc") or "",
                        ch.get("visual_prompt") or "",
                        role,
                    )
                    break
        except Exception:
            pass
    # 明确男性角色绝不用女声通路（即使 api_voice 误落到 shimmer）
    force_male = gender_guess == "onyx" or api_voice in ("onyx", "echo", "ash", "fable")
    force_female = (not force_male) and (
        gender_guess == "shimmer"
        or role in ("旁白", "OS", "os")
        or api_voice in ("nova", "shimmer", "coral", "alloy", "sage")
    )
    want_young_female = force_female and not force_male
    if force_male and api_voice in ("nova", "shimmer", "coral", "alloy", "sage", ""):
        api_voice = "onyx"

    async def _edge(voice_id: str, rate: str) -> None:
        import edge_tts

        comm = edge_tts.Communicate(content, voice_id, rate=rate)
        await comm.save(str(dest))

    # 中文对白：优先 edge 分性别音色（男云希 / 女晓晓），避免 HTTP 音色性别含糊
    if _has_cjk(content):
        if force_male:
            edge_voice = os.environ.get("DRAMA10_MALE_EDGE_VOICE") or "zh-CN-YunxiNeural"
            edge_rate = os.environ.get("DRAMA10_MALE_EDGE_RATE") or "+8%"
        elif want_young_female:
            edge_voice = (
                os.environ.get("DRAMA10_NARRATOR_EDGE_VOICE") or "zh-CN-XiaoxiaoNeural"
            )
            edge_rate = os.environ.get("DRAMA10_NARRATOR_EDGE_RATE") or "+10%"
        else:
            edge_voice = os.environ.get("DRAMA10_MALE_EDGE_VOICE") or "zh-CN-YunxiNeural"
            edge_rate = os.environ.get("DRAMA10_EDGE_RATE") or "+10%"
        try:
            asyncio.run(_edge(edge_voice, edge_rate))
            if dest.is_file() and dest.stat().st_size > 128:
                print(
                    f"[drama10_tts] edge role={role!r} gender={'M' if force_male else 'F' if want_young_female else '?'} voice={edge_voice}",
                    flush=True,
                )
                _apply_role_voice_fx(dest, role)
                return
        except Exception as e:
            err.append(f"edge-tts: {e}")

    try:
        from .character_ohmygpt import generate_character_voice_file

        http_voice = api_voice
        if want_young_female and http_voice in ("", "nova", "onyx"):
            http_voice = (
                os.environ.get("DRAMA10_NARRATOR_VOICE") or "shimmer"
            ).strip() or "shimmer"
        if force_male and http_voice in ("", "nova", "shimmer", "coral", "alloy", "sage"):
            http_voice = "onyx"
        generate_character_voice_file(dest, text=content, voice=http_voice)
        if dest.is_file() and dest.stat().st_size > 128:
            print(
                f"[drama10_tts] http role={role!r} voice={http_voice}",
                flush=True,
            )
            _apply_role_voice_fx(dest, role)
            return
    except Exception as e:
        err.append(f"HTTP TTS: {e}")

    try:
        if want_young_female:
            voice_id = (
                os.environ.get("DRAMA10_NARRATOR_EDGE_VOICE") or "zh-CN-XiaoxiaoNeural"
            )
            rate = os.environ.get("DRAMA10_NARRATOR_EDGE_RATE") or "+10%"
        else:
            voice_id = os.environ.get("DRAMA10_MALE_EDGE_VOICE") or "zh-CN-YunxiNeural"
            rate = os.environ.get("DRAMA10_MALE_EDGE_RATE") or "+8%"
        asyncio.run(_edge(voice_id, rate))
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
