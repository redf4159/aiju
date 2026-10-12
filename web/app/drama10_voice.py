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
    # 默认年轻女声（国漫女主第一人称旁白）；史诗男旁白可设 DRAMA10_NARRATOR_VOICE=onyx
    "旁白": "shimmer",
    "OS": "shimmer",
    "os": "shimmer",
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


def infer_api_voice_from_gender_text(*texts: str) -> Optional[str]:
    """从中文音色/外形描述推断男/女 TTS（避免男角误用女声）。"""
    blob = " ".join((t or "") for t in texts)
    if not blob.strip():
        return None
    female_keys = (
        "女性",
        "女声",
        "女主",
        "少女",
        "姑娘",
        "小姐",
        "夫人",
        "女扮",
        "清冷微哑女",
        "甜软",
        "九亭",  # 晏九亭：女主，避免与晏承嗣男声串
        "郡主",
        "公主",
        "丫头",
    )
    male_keys = (
        "男性",
        "男声",
        "公子",
        "少爷",
        "侯爷",
        "硬朗男",
        "青年男",
        "中年男",
        "成年男",
        "浑厚",
        "冷冽沉稳",
        "承嗣",  # 晏承嗣：男
    )
    # 先男后女：描述里常同时出现「男女」群像词时以更具体的单人性别为准
    hit_f = any(k in blob for k in female_keys)
    hit_m = any(k in blob for k in male_keys)
    if hit_m and not hit_f:
        return "onyx"
    if hit_f and not hit_m:
        return "shimmer"
    if hit_m and hit_f:
        # 「青年男性…」优先于笼统「女」
        if re.search(r"男性|男声|公子|少爷|侯爷|硬朗男", blob):
            return "onyx"
        return "shimmer"
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
        # 项目里若有女主已设 TTS，旁白跟女主（第一人称）
        if project_id:
            try:
                from . import services

                for ch in services.list_characters(project_id):
                    name = (ch.get("name") or "").strip()
                    tv = (ch.get("tts_voice") or "").strip().lower()
                    if tv in OPENAI_TTS_VOICES and name and name not in (
                        "旁白",
                        "OS",
                        "os",
                    ):
                        # 优先带「晚/女/苏」等女主名，否则用第一个已设女声角色
                        if any(k in name for k in ("晚", "苏", "女", "姐", "妹")):
                            return tv
                for ch in services.list_characters(project_id):
                    tv = (ch.get("tts_voice") or "").strip().lower()
                    if tv in ("nova", "shimmer", "coral", "alloy", "sage"):
                        return tv
            except Exception:
                pass
        return (
            os.environ.get("DRAMA10_NARRATOR_VOICE") or DEFAULT_ROLE_API_VOICE["旁白"]
        ).strip() or "shimmer"
    if role in ("大臣们", "群臣"):
        return (
            os.environ.get("DRAMA10_CROWD_VOICE") or DEFAULT_ROLE_API_VOICE["大臣们"]
        ).strip() or "onyx"
    if project_id:
        try:
            from . import services

            chars = list(services.list_characters(project_id))
            # 1) 精确角色名
            for ch in chars:
                name = (ch.get("name") or "").strip()
                if name != role:
                    continue
                tv = (ch.get("tts_voice") or "").strip().lower()
                if tv in OPENAI_TTS_VOICES:
                    return tv
                parsed = parse_api_voice_from_desc(ch.get("voice_desc") or "")
                if parsed:
                    return parsed
                guessed = infer_api_voice_from_gender_text(
                    ch.get("voice_desc") or "", ch.get("visual_prompt") or "", name
                )
                if guessed:
                    return guessed
            # 2) 避免「晏」这类姓氏互相误匹配：仅允许较长包含关系
            for ch in chars:
                name = (ch.get("name") or "").strip()
                if not name or name == role:
                    continue
                if len(name) >= 2 and len(role) >= 2 and (role in name or name in role):
                    # 排除「晏九亭」误吃「晏承嗣」：两边都含姓但其余不同
                    if name[0] == role[0] and name != role and len(name) <= 4 and len(role) <= 4:
                        continue
                    tv = (ch.get("tts_voice") or "").strip().lower()
                    if tv in OPENAI_TTS_VOICES:
                        return tv
                    parsed = parse_api_voice_from_desc(ch.get("voice_desc") or "")
                    if parsed:
                        return parsed
                    guessed = infer_api_voice_from_gender_text(
                        ch.get("voice_desc") or "", name
                    )
                    if guessed:
                        return guessed
        except Exception:
            pass
    guessed = infer_api_voice_from_gender_text(role)
    if guessed:
        return guessed
    if role_key in DEFAULT_ROLE_API_VOICE:
        return DEFAULT_ROLE_API_VOICE[role_key]
    if role in DEFAULT_ROLE_API_VOICE:
        return DEFAULT_ROLE_API_VOICE[role]
    return (os.environ.get("CHAR_TTS_VOICE") or DEFAULT_ROLE_API_VOICE["默认"]).strip() or "echo"
