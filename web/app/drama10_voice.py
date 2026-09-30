# -*- coding: utf-8
"""全片 TTS 音色：旁白固定、角色按人物设定或默认表统一。"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

OPENAI_TTS_VOICES = frozenset(
    {
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
    }
)

# 与 Comfy Drama10 人物表一致；可被环境变量 DRAMA10_ROLE_VOICES_JSON 覆盖
DEFAULT_ROLE_API_VOICE: Dict[str, str] = {
    "旁白": "onyx",
    "OS": "onyx",
    "os": "onyx",
    "凌川": "echo",
    "神秘黑影": "onyx",
    "黑影": "onyx",
    "神秘声音": "onyx",
    "卡索": "echo",
    "樱空释": "fable",
    "皇北城": "fable",
    "侍卫": "ash",
    "大臣": "echo",
    "大臣们": "onyx",
    "群臣": "onyx",
    "默认": "echo",
}

SHADOW_ROLES = frozenset({"神秘黑影", "黑影", "神秘声音"})


def _role_voice_overrides() -> Dict[str, str]:
    raw = (os.environ.get("DRAMA10_ROLE_VOICES_JSON") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k).strip(): str(v).strip() for k, v in data.items() if v}
    except json.JSONDecodeError:
        pass
    return {}


def parse_api_voice_from_desc(voice_desc: str) -> Optional[str]:
    """从人物「声音描述」解析 OpenAI 兼容 voice id（如 TTS:onyx 或句中含 onyx）。"""
    s = (voice_desc or "").strip()
    if not s:
        return None
    m = re.search(r"(?:TTS|tts|音色)\s*[：:=]\s*([A-Za-z]+)", s)
    if m:
        v = m.group(1).strip().lower()
        if v in OPENAI_TTS_VOICES:
            return v
    for token in re.findall(r"[A-Za-z]+", s):
        low = token.lower()
        if low in OPENAI_TTS_VOICES:
            return low
    return None


def resolve_api_voice(role: str, *, project_id: Optional[str] = None) -> str:
    from .voice_cast import ROLE_ALIASES, cast_item_for_role, voice_cast_from_prefs

    role = (role or "旁白").strip()
    role_key = ROLE_ALIASES.get(role, role)
    overrides = _role_voice_overrides()
    if role in overrides:
        return overrides[role]
    if role_key in overrides:
        return overrides[role_key]
    if project_id:
        try:
            from . import services

            prefs = services.get_generation_prefs(project_id)
            item = cast_item_for_role(voice_cast_from_prefs(prefs), role_key)
            if item and item.get("tts_voice") in OPENAI_TTS_VOICES:
                return str(item["tts_voice"])
        except Exception:
            pass
    if role in ("旁白", "OS", "os"):
        return (
            os.environ.get("DRAMA10_NARRATOR_VOICE") or DEFAULT_ROLE_API_VOICE["旁白"]
        ).strip() or "onyx"
    if role in ("大臣们", "群臣"):
        return (
            os.environ.get("DRAMA10_CROWD_VOICE") or DEFAULT_ROLE_API_VOICE["大臣们"]
        ).strip() or "onyx"
    if project_id:
        try:
            from . import services

            for ch in services.list_characters(project_id):
                name = (ch.get("name") or "").strip()
                if not name:
                    continue
                if name == role or role in name or name in role:
                    tv = (ch.get("tts_voice") or "").strip().lower()
                    if tv in OPENAI_TTS_VOICES:
                        return tv
                    parsed = parse_api_voice_from_desc(ch.get("voice_desc") or "")
                    if parsed:
                        return parsed
        except Exception:
            pass
    if role_key in DEFAULT_ROLE_API_VOICE:
        return DEFAULT_ROLE_API_VOICE[role_key]
    if role in DEFAULT_ROLE_API_VOICE:
        return DEFAULT_ROLE_API_VOICE[role]
    return (os.environ.get("CHAR_TTS_VOICE") or DEFAULT_ROLE_API_VOICE["默认"]).strip() or "echo"
