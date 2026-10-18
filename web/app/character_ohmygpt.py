# -*- coding: utf-8 -*-
"""人物/场景/道具库：OhMyGPT 设定组合图 + OpenAI 兼容 TTS。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx

from .asset_sheet_prompts import (
    DEFAULT_ART_STYLE,
    AssetKind,
    SheetMode,
    compose_sheet_api_prompt,
    sheet_canvas,
)
from .storyboard_image_api import (
    generate_still_to_file,
    image_api_model,
)


def _infer_kind(angle_label: str, role_note: str) -> AssetKind:
    al = (angle_label or "").strip()
    rn = (role_note or "").strip()
    if "场景" in al or "母景" in al or "空镜" in rn:
        return "scene"
    if "道具" in al or "物品" in al:
        return "prop"
    return "character"


def _character_image_prompt(
    *,
    name: str,
    visual_prompt: str,
    role_note: str,
    style_prefix: str,
    prefs: dict,
    angle_label: str,
    extra: str,
    kind: Optional[AssetKind] = None,
    sheet_mode: SheetMode = "body",
) -> tuple[str, str]:
    # 使用本项目画风，不再强制拼接全局「半写实3D国漫」
    art = (style_prefix or "").strip() or DEFAULT_ART_STYLE
    k = kind or _infer_kind(angle_label, role_note)
    appearance = (visual_prompt or "").strip() or (role_note or "").strip()
    mode: SheetMode = "head" if (sheet_mode or "body") == "head" else "body"
    return compose_sheet_api_prompt(
        kind=k,
        name=name,
        visual_prompt=appearance,
        role_note=role_note or "",
        art_style=art,
        angle_label=angle_label,
        extra=extra,
        sheet_mode=mode,
    )


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
    kind: Optional[AssetKind] = None,
    sheet_mode: SheetMode = "body",
    ref_image: Optional[Path] = None,
) -> Path:
    prompt, neg = _character_image_prompt(
        name=name,
        visual_prompt=visual_prompt,
        role_note=role_note,
        style_prefix=style_prefix,
        prefs=prefs,
        angle_label=angle_label,
        extra=extra,
        kind=kind,
        sheet_mode=sheet_mode,
    )
    if ref_image is not None and Path(ref_image).is_file():
        prompt = (
            "【硬性一致性·同一个人】人物脸部必须与已提交的基础设定参考图为同一人："
            "同一脸型、五官比例、眼型、鼻型、嘴型、瞳色与肤色；"
            "禁止换脸；仅允许本状态要求的发型、服装与年龄感差异。"
            + prompt
        )
    # 设定组合图强制 16:9 横屏，避免竖屏项目把三视图压成竖幅
    sw, sh = sheet_canvas(max(width, 1280), max(height if width >= height else width, 720))
    if width >= height and width >= 640 and height >= 360:
        sw, sh = sheet_canvas(width, height)
    return generate_still_to_file(
        prompt,
        neg,
        sw,
        sh,
        dest,
        model=model or image_api_model(prefs),
        ref_image=ref_image,
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
    from .storyboard_image_api import _resolve_api_credentials

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
