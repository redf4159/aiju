# -*- coding: utf-8 -*-
"""项目级生成偏好（全局记忆，注入 Drama10 / ComfyUI）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

LIVE_ACTION_BASE = (
    "真人写实电影摄影，自然光影，电影级景深，"
    "横屏16比9构图，地平线水平，禁止画面旋转"
)
LIVE_ACTION_CHARACTER = "真实中国演员面孔与肤质，人物朝向自然"
LIVE_ACTION_PREFIX = f"{LIVE_ACTION_BASE}，{LIVE_ACTION_CHARACTER}"

ESTABLISHING_STYLE = (
    "远景空镜，以环境建筑与天气为主，画面无人物或无清晰人脸，"
    "不要居中肖像，不要人物占满画幅"
)
ESTABLISHING_STILL_HINT = (
    "环境气氛定场镜头，无人物或无清晰人脸，禁止主角/王子居中肖像，建筑与远景为主"
)
ESTABLISHING_STILL_PREFIX = (
    "超广角大全景远景航拍，冰雪王城与环境占满画幅；"
    "人物占画面小于5%或完全没有人；禁止中景/近景/半身像；禁止白发王子铠甲男性在前景"
)
ESTABLISHING_NEGATIVE = (
    "人物特写, 主角居中, 王子, 白发, 银发, 铠甲, 人像, 半身, 肖像, 男性面部占满画面, "
    "hero portrait, character close-up, handsome man foreground, warrior close-up"
)
CROWD_WIDE_HINT = (
    "电影广角中远景，景深明显，主角中景清晰，两侧众人脸小或略虚化，"
    "避免多张扭曲大脸，群像不做面部特写"
)

DEFAULT_NEGATIVE = (
    "禁止动漫、卡通、二次元、插画、赛璐璐、3D游戏渲染感、Q版、"
    "横屏错误、画面侧翻、文字字幕水印、"
    "畸形脸、五官扭曲、面部融化、多余眼睛、错位五官、坏手坏脸、low quality face"
)

FACE_SHOT_HINT = (
    "电影剧照，真人面孔五官端正对称，双眼清晰，皮肤自然，"
    "中近景或标准特写（避免极端大特写导致变形）"
)

VIDEO_SCENE_INTEGRATION = (
    "同一实拍摄影机连续拍摄：单一曝光与白平衡、统一胶片颗粒与大气透视；"
    "人物完全处在场景三维空间内，脚下接地阴影、环境反射与背景色温一致，"
    "景深光学连续，边缘无硬切无描边光晕；"
    "严禁抠像、严禁绿幕合成感、严禁人物前景层贴背景、严禁两层分离合成或贴纸感"
)

PORTRAIT_HINT = "横屏16比9构图，主体 upright，地平线水平，禁止旋转90度"
LANDSCAPE_HINT = "横屏16比9环境构图，地平线水平，禁止旋转90度，空镜无人物"


def default_prefs() -> Dict[str, Any]:
    from .voice_cast import DEFAULT_VOICE_CAST

    return {
        "look": "live_action",
        "storyboard_backend": "image_api",
        "storyboard_image_model": "",
        "extra_prefix": "",
        "negative_prompt": DEFAULT_NEGATIVE,
        "global_memory": "",
        "voice_cast": list(DEFAULT_VOICE_CAST),
    }


def parse_prefs(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
    out = default_prefs()
    out.update({k: v for k, v in data.items() if v is not None})
    return out


def validate_still_not_near_black(path: Path) -> None:
    """Reject accidental all-black API/Comfy outputs (not script-intended black shots)."""
    from PIL import Image

    with Image.open(path) as im:
        rgb = im.convert("RGB").resize((48, 48))
        pixels = list(rgb.getdata())
    lumas = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
    if not lumas:
        raise RuntimeError("出图结果为空，已拒绝保存")
    if max(lumas) <= 8:
        raise RuntimeError("出图结果为近黑图，已拒绝保存（请重试或检查 Comfy/API）")


def _is_black_shot(visual: str) -> bool:
    v = (visual or "").strip()
    if not v:
        return False
    if any(
        x in v
        for x in ("无切黑", "不切黑", "无黑场", "不要黑场", "非黑场", "无纯黑")
    ):
        return False
    return any(x in v for x in ("纯黑", "黑场", "切黑", "全黑画面", "fade to black"))


def _is_establishing_shot(visual: str) -> bool:
    v = (visual or "").strip()
    if not v:
        return False
    if any(x in v for x in ("无人物", "空镜", "纯环境", "无主角", "无人物出镜")):
        return True
    if any(x in v for x in ("定场", "史诗定场", "establishing shot", "establishing")):
        return True
    char_markers = (
        "卡索",
        "皇北城",
        "人脸",
        "面孔",
        "侧脸",
        "肖像",
        "群像",
        "跪拜",
        "王袍",
        "人像",
        "手握",
        "眼神",
        "耳语",
    )
    if any(c in v for c in char_markers):
        return False
    if any(x in v for x in ("俯拍", "航拍", "大全景", "aerial", "自上而下")):
        return True
    return False


def _is_crowd_wide_shot(visual: str) -> bool:
    v = (visual or "").strip()
    if any(x in v for x in ("特写", "大特写", "面部清晰", "五官端正")):
        return False
    return any(x in v for x in ("万民", "群像", "跪拜", "群臣", "中远景", "广角"))


def style_prefix_for_shot(visual: str, proj_style: str, prefs: Dict[str, Any]) -> str:
    """按镜头区分：空镜/远景不加「演员面孔」类前缀。"""
    parts: list[str] = []
    if prefs.get("look") == "live_action":
        parts.append(LIVE_ACTION_BASE)
        if _is_establishing_shot(visual):
            parts.append(ESTABLISHING_STYLE)
        elif _is_crowd_wide_shot(visual):
            parts.append(CROWD_WIDE_HINT)
        else:
            parts.append(LIVE_ACTION_CHARACTER)
    if (proj_style or "").strip():
        parts.append(proj_style.strip())
    if (prefs.get("extra_prefix") or "").strip():
        parts.append(prefs["extra_prefix"].strip())
    if (prefs.get("global_memory") or "").strip():
        parts.append(prefs["global_memory"].strip())
    return "，".join(p for p in parts if p)


def effective_style(style_prefix: str, prefs: Dict[str, Any]) -> Tuple[str, str]:
    """整集默认风格（含人物向前缀）；单镜出图请用 style_prefix_for_shot。"""
    parts = []
    if prefs.get("look") == "live_action":
        parts.append(LIVE_ACTION_PREFIX)
    if (style_prefix or "").strip():
        parts.append(style_prefix.strip())
    if (prefs.get("extra_prefix") or "").strip():
        parts.append(prefs["extra_prefix"].strip())
    if (prefs.get("global_memory") or "").strip():
        parts.append(prefs["global_memory"].strip())
    neg = (prefs.get("negative_prompt") or DEFAULT_NEGATIVE).strip()
    return "，".join(parts), neg


_CLOSEUP_KW = ("特写", "大特写", "面部", "眼神", "侧脸", "face", "close-up", "close up", "extreme close")


def _needs_face_hint(visual: str) -> bool:
    v = (visual or "").lower()
    return any(k in v for k in _CLOSEUP_KW)


def apply_negative_to_visual(
    visual: str, negative: str, *, establishing: Optional[bool] = None
) -> str:
    v = (visual or "").strip()
    est = _is_establishing_shot(v) if establishing is None else establishing
    hint = LANDSCAPE_HINT if est else PORTRAIT_HINT
    if not v:
        return f"{hint}。{negative}"
    if hint not in v and PORTRAIT_HINT not in v and LANDSCAPE_HINT not in v:
        v = f"{hint}，{v}"
    if not est and _is_crowd_wide_shot(v):
        if CROWD_WIDE_HINT not in v:
            v = f"{CROWD_WIDE_HINT}，{v}"
    elif not est and _needs_face_hint(v):
        if FACE_SHOT_HINT not in v:
            v = f"{FACE_SHOT_HINT}，{v.replace('大特写', '中近景')}"
    if est:
        if "无人物" not in v:
            v = f"{v}，无人物空镜"
        neg = f"{negative}，{ESTABLISHING_NEGATIVE}"
    else:
        neg = negative
    return f"{v}。避免：{neg}"
