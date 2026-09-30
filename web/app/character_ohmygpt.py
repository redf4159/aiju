# -*- coding: utf-8 -*-
"""人物库：OhMyGPT 文生图 + OpenAI 兼容 TTS。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx

from .generation_prefs import LIVE_ACTION_PREFIX, effective_style
from .storyboard_image_api import (
    _merge_prompt,
    _resolve_api_credentials,
    generate_still_to_file,
    image_api_model,
)


def _character_image_prompt(
    *,
    name: str,
    visual_prompt: str,
    role_note: str,
    style_prefix: str,
    prefs: dict,
    angle_label: str,
    extra: str,
) -> tuple[str, str]:
    angle = (angle_label or "正面").strip()
    sp, neg = effective_style(style_prefix, prefs)
    parts = [
        LIVE_ACTION_PREFIX,
        sp,
        f"单人角色设定参考图，{angle}视角",
        f"角色：{name}",
    ]
    if role_note.strip():
        parts.append(role_note.strip())
    if visual_prompt.strip():
        parts.append(visual_prompt.strip())
    if extra.strip():
        parts.append(extra.strip())
    parts.append("横屏16比9半身或全身，背景简洁，无文字无水印，电影剧照质感")
    return _merge_prompt("，".join(parts), neg), neg


def generate_character_portrait_file(
    dest: Path,
    *,
    name: str,
    visual_prompt: str,
    role_note: str,
    style_prefix: str,
    prefs: dict,
    width: int,
    height: int,
    angle_label: str = "正面",
    extra: str = "",
    model: Optional[str] = None,
) -> Path:
    prompt, neg = _character_image_prompt(
        name=name,
        visual_prompt=visual_prompt,
        role_note=role_note,
        style_prefix=style_prefix,
        prefs=prefs,
        angle_label=angle_label,
        extra=extra,
    )
    return generate_still_to_file(
        prompt,
        neg,
        width,
        height,
        dest,
        model=model or image_api_model(prefs),
    )


def _tts_model() -> str:
    return (
        os.environ.get("CHAR_TTS_MODEL")
        or os.environ.get("OHMYGPT_TTS_MODEL")
        or "tts-1"
    ).strip()


def _tts_voice(voice: Optional[str]) -> str:
    v = (voice or os.environ.get("CHAR_TTS_VOICE") or "nova").strip()
    return v or "nova"


def generate_character_voice_file(
    dest: Path,
    *,
    text: str,
    voice: Optional[str] = None,
    timeout: float = 120.0,
) -> Path:
    base, key = _resolve_api_credentials()
    if not key:
        raise RuntimeError(
            "未配置 OhMyGPT：请在「全局制作 → OhMyGPT 密钥」保存 API Key"
        )
    content = (text or "").strip()
    if len(content) < 2:
        raise ValueError("试听台词太短")
    if len(content) > 800:
        content = content[:800]
    url = f"{base.rstrip('/')}/audio/speech"
    body = {
        "model": _tts_model(),
        "input": content,
        "voice": _tts_voice(voice),
    }
    fmt = (os.environ.get("CHAR_TTS_FORMAT") or "mp3").strip().lower()
    if fmt in ("mp3", "opus", "aac", "flac", "wav", "pcm"):
        body["response_format"] = fmt
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    proxy = (os.environ.get("STORYBOARD_IMAGE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY") or "").strip()
    client_kw: dict = {"timeout": httpx.Timeout(timeout, connect=min(60.0, timeout))}
    if proxy:
        client_kw["proxy"] = proxy
    with httpx.Client(**client_kw) as client:
        resp = client.post(url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"TTS HTTP {resp.status_code}: {resp.text[:500]}")
        audio = resp.content
    if len(audio) < 128:
        raise RuntimeError("TTS 返回空音频")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(audio)
    return dest
