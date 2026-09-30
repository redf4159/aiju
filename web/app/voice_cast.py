# -*- coding: utf-8
"""全剧固定音色：项目级 + 分集 JSON，同步到人物库并供 TTS 解析。"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from .drama10_voice import OPENAI_TTS_VOICES

# 《冰境》默认卡司（20 集尽量不变）
DEFAULT_VOICE_CAST: List[Dict[str, Any]] = [
    {
        "role": "旁白",
        "tts_voice": "onyx",
        "voice_desc": "男性电影预告片式旁白，沉稳、低沉；史诗、神秘。TTS:onyx",
        "emotion": "沉稳史诗",
        "scope": "series",
    },
    {
        "role": "凌川",
        "tts_voice": "echo",
        "voice_desc": "年轻男性20多岁，偏低沉、干净、有磁性。TTS:echo",
        "emotion": "前期疑惑恐惧，后期震惊",
        "scope": "series",
    },
    {
        "role": "神秘黑影",
        "tts_voice": "onyx",
        "voice_desc": "极低沉男性，空灵遥远，大空间混响。TTS:onyx",
        "emotion": "神秘、压迫",
        "scope": "series",
    },
]

ROLE_ALIASES = {
    "黑影": "神秘黑影",
    "神秘声音": "神秘黑影",
}


def normalize_cast_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    role = (item.get("role") or item.get("name") or "").strip()
    if not role:
        return None
    role = ROLE_ALIASES.get(role, role)
    tv = (item.get("tts_voice") or "").strip().lower()
    if tv and tv not in OPENAI_TTS_VOICES:
        tv = ""
    desc = (item.get("voice_desc") or "").strip()
    emo = (item.get("emotion") or "").strip()
    if emo and emo not in desc:
        desc = f"{desc} 情绪：{emo}。".strip() if desc else f"情绪：{emo}。"
    scope = (item.get("scope") or "series").strip() or "series"
    return {
        "role": role,
        "tts_voice": tv,
        "voice_desc": desc,
        "emotion": emo,
        "scope": scope,
    }


def normalize_cast(raw: Any) -> List[Dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        n = normalize_cast_item(item)
        if not n or n["role"] in seen:
            continue
        seen.add(n["role"])
        out.append(n)
    return out


def merge_voice_cast(
    project_cast: Any,
    episode_cast: Any = None,
) -> List[Dict[str, Any]]:
    """分集条目覆盖同 role 的全剧条目。"""
    base = normalize_cast(project_cast) or list(DEFAULT_VOICE_CAST)
    ep = normalize_cast(episode_cast)
    if not ep:
        return base
    by_role = {x["role"]: x for x in base}
    for x in ep:
        by_role[x["role"]] = x
    return list(by_role.values())


def cast_item_for_role(cast: List[Dict[str, Any]], role: str) -> Optional[Dict[str, Any]]:
    role = (role or "").strip()
    if not role:
        return None
    role = ROLE_ALIASES.get(role, role)
    for item in cast:
        if item.get("role") == role:
            return item
    for item in cast:
        r = item.get("role") or ""
        if role in r or r in role:
            return item
    return None


def sync_voice_cast_to_characters(
    project_id: str,
    cast: List[Dict[str, Any]],
    *,
    episode_id: str = "",
) -> None:
    """把音色表写入人物库（同名更新 tts_voice / voice_desc）。"""
    from . import services

    cast = normalize_cast(cast)
    if not cast:
        return
    existing = services.list_characters(project_id)
    by_name = {(c.get("name") or "").strip(): c for c in existing}

    for i, item in enumerate(cast):
        name = item["role"]
        ch = by_name.get(name)
        note = (item.get("emotion") or "").strip()
        payload: Dict[str, Any] = {
            "id": ch["id"] if ch else str(uuid.uuid4()),
            "name": name,
            "role_note": note or (ch.get("role_note") if ch else ""),
            "visual_prompt": (ch.get("visual_prompt") if ch else "") or "",
            "voice_desc": item.get("voice_desc") or "",
            "tts_voice": item.get("tts_voice") or "",
            "voice_samples": ch.get("voice_samples") if ch else [],
            "ref_images": ch.get("ref_images") if ch else [],
            "sort_order": int(ch.get("sort_order") if ch else i),
            "episode_id": episode_id if (item.get("scope") == "episode") else "",
        }
        if ch and ch.get("episode_id") and not episode_id:
            payload["episode_id"] = ch.get("episode_id") or ""
        services.upsert_character(project_id, payload)


def voice_cast_from_prefs(prefs: Dict[str, Any]) -> List[Dict[str, Any]]:
    return merge_voice_cast(prefs.get("voice_cast"), None)


def enrich_voice_line(v: Dict[str, Any], cast: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对白行补全 tts_voice / voice_desc / emotion（不覆盖已有值）。"""
    out = dict(v or {})
    role = (out.get("role") or "").strip()
    if not role or role in ("—", "音效", "环境"):
        return out
    item = cast_item_for_role(cast, role)
    if not item:
        return out
    for key in ("tts_voice", "voice_desc", "emotion"):
        if not str(out.get(key) or "").strip():
            out[key] = item.get(key) or ""
    return out


def _roles_mentioned_in_shot(shot: Dict[str, Any]) -> List[str]:
    blob = " ".join(
        str(shot.get(k) or "")
        for k in ("visual", "performance", "motion", "title")
    )
    found: List[str] = []
    for name in ("旁白", "凌川", "神秘黑影", "黑影"):
        if name in blob:
            canon = ROLE_ALIASES.get(name, name)
            if canon not in found:
                found.append(canon)
    return found


def enrich_shot_voices(shot: Dict[str, Any], cast: List[Dict[str, Any]]) -> Dict[str, Any]:
    """单镜 voices 补音色；无对白镜按出镜角色或环境补说明行（text 为空，不触发 TTS）。"""
    out = dict(shot)
    raw = out.get("voices")
    voices: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for v in raw:
            if isinstance(v, dict):
                voices.append(enrich_voice_line(v, cast))

    if voices:
        out["voices"] = voices
        return out

    roles = _roles_mentioned_in_shot(out)
    if roles:
        for role in roles:
            item = cast_item_for_role(cast, role) or {}
            voices.append(
                {
                    "role": role,
                    "text": "",
                    "note": "本镜无对白，仅表演/环境；音色供整集统一参考",
                    "tts_voice": item.get("tts_voice") or "",
                    "voice_desc": item.get("voice_desc") or "",
                    "emotion": item.get("emotion") or "",
                }
            )
    else:
        amb = (out.get("ambience") or "").strip()
        voices.append(
            {
                "role": "环境",
                "text": "",
                "note": "无人物对白；纯环境/特效声" + (f"（{amb[:72]}）" if amb else ""),
                "tts_voice": "",
                "voice_desc": "不用 TTS，仅 ambience/SFX",
                "emotion": "",
            }
        )
    out["voices"] = voices
    return out


def enrich_episode_shots(ep: Dict[str, Any], cast: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(ep)
    shots = []
    for s in ep.get("shots") or []:
        if isinstance(s, dict):
            shots.append(enrich_shot_voices(s, cast))
    out["shots"] = shots
    return out
