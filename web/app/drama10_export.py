# -*- coding: utf-8 -*-
"""Export episode JSON → ComfyUI Drama10 multiline prompt."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

NARRATOR_ROLES = frozenset({"旁白", "OS", "os", "narrator", "Narrator"})


def _is_narrator_role(role: str) -> bool:
    return (role or "").strip() in NARRATOR_ROLES


def _narrator_only_lip_hint() -> str:
    return (
        "旁白为画外音轨：画面内所有人物嘴唇全程闭合静止，禁止对口型、禁止说话式张嘴；"
        "仅自然呼吸、眨眼与环境微动，嘴部肌肉不随旁白节奏变化。"
    )


def _on_screen_dialogue_voices(voices: list) -> list:
    return [
        v
        for v in _dedupe_voices(voices if isinstance(voices, list) else [])
        if not _is_narrator_role(v.get("role") or "")
    ]


def _dialogue_facing_hint(shot: dict[str, Any]) -> str:
    """对白朝向：说话人看向听话人，禁止正对镜头念白。"""
    dialogue = _on_screen_dialogue_voices(shot.get("voices") or [])
    if not dialogue:
        return ""
    roles: List[str] = []
    for v in dialogue:
        r = (v.get("role") or "").strip()
        if r and r not in roles:
            roles.append(r)
    if len(roles) >= 2:
        a, b = roles[0], roles[1]
        return (
            f"站位对谈轴：{a}与{b}互为左右对位，说话时脸与肩朝向对方，"
            f"眼线落在对方脸/肩；禁止任何人正对镜头念台词。"
        )
    role = roles[0] if roles else "角色"
    return (
        f"{role}朝向画内对谈对象或事件方向说话，眼线不看镜头；"
        f"禁止正对镜头念白。"
    )


def _lip_motion_hint(shot: dict[str, Any]) -> str:
    raw = shot.get("voices") or []
    dialogue = _on_screen_dialogue_voices(raw)
    has_narrator = any(
        _is_narrator_role(v.get("role") or "") and (v.get("text") or "").strip()
        for v in raw
        if isinstance(v, dict)
    )
    face = _dialogue_facing_hint({"voices": raw})
    face_bit = f"{face} " if face else ""
    if not dialogue:
        if has_narrator:
            return _narrator_only_lip_hint()
        return "无对白：人物口型静止，仅呼吸微动。"
    if len(dialogue) == 1:
        role = (dialogue[0].get("role") or "角色").strip()
        if role in ("大臣们",):
            return "多名跪拜大臣齐声朝贺，群体张口齐呼；非说话者闭口。"
        extra = "画外旁白不对口型。" if has_narrator else ""
        return f"{face_bit}仅{role}口型随画内对白开合，其他人物闭口。{extra}"
    seq = "→".join((v.get("role") or "角色") for v in dialogue)
    extra = "画外旁白不对口型。" if has_narrator else ""
    return f"{face_bit}画内对白顺序({seq})，同一时刻仅一人口型在动。{extra}"


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


def _texts_equivalent(a: str, b: str) -> bool:
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 12 and (na in nb or nb in na):
        return True
    return False


def _prepend_style_once(style_prefix: str, scene: str) -> str:
    sp = (style_prefix or "").strip()
    scene = (scene or "").strip()
    if not sp:
        return scene
    if _texts_equivalent(sp, scene) or sp in scene:
        return scene
    head = _norm_text(sp)[:24]
    if head and head in _norm_text(scene):
        return scene
    return f"{sp}，{scene}".strip("，")


def _dedupe_voices(voices: list) -> list:
    out: list = []
    seen: set[tuple] = set()
    for v in voices or []:
        if not isinstance(v, dict):
            continue
        role = (v.get("role") or "默认").strip()
        text = (v.get("text") or "").strip()
        if not text:
            continue
        st = v.get("start_sec")
        try:
            st_key = round(float(st), 2) if st is not None else None
        except (TypeError, ValueError):
            st_key = None
        key = (role, _norm_text(text), st_key)
        if key in seen:
            continue
        seen.add(key)
        item = {"role": role, "text": text}
        if st_key is not None:
            item["start_sec"] = float(st_key)
        out.append(item)
    return out


def _strip_voice_text_from_prose(text: str, voices: list) -> str:
    p = (text or "").strip()
    if not p:
        return ""
    for v in voices or []:
        t = (v.get("text") or "").strip()
        if not t:
            continue
        for pat in (
            t,
            f"「{t}」",
            f'"{t}"',
            f"“{t}”",
        ):
            p = p.replace(pat, "")
        if len(t) >= 4:
            p = re.sub(r"[^。\n；]*" + re.escape(t[: min(12, len(t))]) + r"[^。\n；]*[。\n；]?", "", p)
    p = re.sub(r"[ \t]+", " ", p)
    p = re.sub(r"\n{3,}", "\n\n", p)
    return p.strip(" ，。\n")


def _sanitize_motion(motion: str, voices: list) -> str:
    m = (motion or "").strip()
    if not m:
        return ""
    m = re.sub(r"^镜头运动[：:]\s*", "", m)
    voices = _dedupe_voices(voices if isinstance(voices, list) else [])
    if voices:
        m = _strip_voice_text_from_prose(m, voices)
        for v in voices:
            role = (v.get("role") or "").strip()
            if role in ("旁白", "OS", "os") and "旁白" in m:
                m = re.sub(r"仅旁白[^。\n]*[。\n]?", "", m)
                m = re.sub(r"无对白[^。\n]*[。\n]?", "", m)
    return m.strip()


def _pick_scene_text(shot: dict[str, Any]) -> str:
    scene = (shot.get("scene") or "").strip()
    visual = (shot.get("visual") or "").strip()
    if not scene:
        return visual
    if not visual or _texts_equivalent(scene, visual):
        return scene
    if _norm_text(visual) in _norm_text(scene):
        return scene
    if _norm_text(scene) in _norm_text(visual):
        return visual
    return f"{visual} {scene}".strip()


def _pick_continuity_text(shot: dict[str, Any], scene_text: str) -> str:
    sid = int(shot.get("id") or shot.get("shot_num") or 0)
    c = (shot.get("continuity") or "").strip()
    if c and not _texts_equivalent(c, scene_text):
        nc = _norm_text(c)
        ns = _norm_text(scene_text)
        if nc not in ns and ns not in nc:
            base = c
        else:
            base = ""
    else:
        base = ""
    if not base:
        base = (
            f"本镜为全片第{sid}镜，与前后镜同一场连续戏：同一空间陈设、同一光色、"
            f"同一人物左右站位轴；只推进表演，禁止换景、禁止站位左右对调、禁止重摆。"
            if sid
            else "与前后镜同一场连续戏：空间/光色/站位轴不变，禁止换景重摆。"
        )
    face = _dialogue_facing_hint(shot)
    if face and face not in base:
        return f"{base} {face}"
    return base


def compact_shot_record(shot: dict[str, Any]) -> dict[str, Any]:
    """保存前去掉镜头表字段间的重复描述（scene/visual、continuity 等）。"""
    out = dict(shot)
    visual = (out.get("visual") or "").strip()
    scene = (out.get("scene") or "").strip()
    if scene and visual and _texts_equivalent(scene, visual):
        out["scene"] = ""
    elif scene and visual and _norm_text(visual) in _norm_text(scene):
        out["visual"] = visual
        out["scene"] = ""
    voices = _dedupe_voices(out.get("voices") or [])
    out["voices"] = voices
    perf = (out.get("performance") or "").strip()
    if perf:
        perf = _strip_voice_text_from_prose(perf, voices)
        hint = _lip_motion_hint({"voices": voices})
        if perf and _texts_equivalent(perf, hint):
            perf = ""
        out["performance"] = perf
    cont = (out.get("continuity") or "").strip()
    scene_eff = _pick_scene_text({**out, "scene": out.get("scene"), "visual": visual})
    if cont and _texts_equivalent(cont, scene_eff):
        out["continuity"] = ""
    motion = _sanitize_motion(out.get("motion") or "", voices)
    out["motion"] = motion
    return out


def _fmt_voice(role: str, text: str, start_sec: Optional[float] = None) -> str:
    role = (role or "默认").strip()
    text = (text or "").strip()
    if not text:
        return ""
    st = ""
    if start_sec is not None:
        try:
            st = f"（{float(start_sec):.1f}秒起）"
        except (TypeError, ValueError):
            st = ""
    if role in ("旁白", "OS", "os"):
        return f"旁白：「{text}」{st}"
    return f"对白：{role}「{text}」{st}"


def _fmt_voice_entry(v: dict[str, Any]) -> str:
    role = (v.get("role") or "默认").strip()
    text = (v.get("text") or "").strip()
    note = (v.get("note") or "").strip()
    tv = (v.get("tts_voice") or "").strip()
    vd = (v.get("voice_desc") or "").strip()
    emo = (v.get("emotion") or "").strip()
    tone = ""
    if tv or vd or emo:
        bits = []
        if tv:
            bits.append(f"TTS:{tv}")
        if emo:
            bits.append(f"情绪:{emo}")
        if vd:
            bits.append(vd[:80])
        av = (v.get("asset_visual") or "").strip()
        if av:
            bits.append(f"外形:{av[:60]}")
        tone = "（" + "；".join(bits) + "）"
    if not text:
        if note:
            return f"音色说明：{role}{tone} — {note}"
        return ""
    line = _fmt_voice(role, text, v.get("start_sec"))
    return f"{line}{tone}" if tone else line


def _h3_native_audio_directive(voices: list, ambience: str) -> str:
    """官方 H3 成片音轨指令：模型直接出旁白/对白 + 环境声（不用 Web TTS 盖轨）。"""
    lines: List[str] = [
        "【H3音轨】Must output a clear stereo soundtrack with intelligible Mandarin Chinese speech "
        "and natural environmental sounds matching the picture.",
        "CRITICAL: Forbidden audio artifacts — electrical buzz/hum, AC mains whine, synthetic drone, "
        "static hiss-only beds, modem-like tones. If unsure, prefer quiet natural wood-room tone + speech.",
        "【禁止】电流声、嗡鸣、合成低频、只有沙沙底噪；对白必须是清晰可辨的普通话人声，音量明显高于环境声。",
        "No on-screen subtitles. Speech lives only on the audio track.",
    ]
    any_speech = False
    for v in _dedupe_voices(voices if isinstance(voices, list) else []):
        if not isinstance(v, dict):
            continue
        text = (v.get("text") or "").strip()
        if not text:
            continue
        any_speech = True
        role = (v.get("role") or "旁白").strip()
        try:
            st = float(v.get("start_sec") if v.get("start_sec") is not None else 0.6)
        except (TypeError, ValueError):
            st = 0.6
        gender_hint = ""
        low = role
        if any(k in low for k in ("承嗣", "道成", "公子", "少爷", "侯爷", "玄策", "男")):
            gender_hint = " (young adult MALE hard voice, not female)"
        elif any(k in low for k in ("九亭", "明珠", "姑娘", "小姐", "夫人", "女")):
            gender_hint = " (young adult FEMALE voice, not male)"
        if _is_narrator_role(role):
            lines.append(
                f'Off-screen Mandarin voiceover MUST be clearly audible starting near {st:.1f}s: "{text}" '
                "while all on-screen characters keep lips completely closed."
            )
        else:
            lines.append(
                f'{role}{gender_hint} MUST speak audible Mandarin on-screen starting near {st:.1f}s: "{text}" '
                "with natural lip sync; other characters stay silent with closed lips. "
                "Voice gender must match the speaking character."
            )
    if any_speech:
        lines.append(
            "Dialogue is mandatory on the soundtrack for this shot; do not replace speech with ambience or hum."
        )
    else:
        lines.append("No dialogue this shot: keep lips closed; soft natural ambience only (no electrical hum).")
    amb = (ambience or "").strip()
    if not amb:
        amb = "quiet wooden interior, soft distant wind, light room tone"
    lines.append(
        f"Ambient bed: {amb}. Keep ambience under speech; continuous natural room tone; "
        "absolutely no electrical hum or synthetic drone."
    )
    return "\n".join(lines)


def is_black_shot_visual(visual: str) -> bool:
    from .generation_prefs import _is_black_shot

    return _is_black_shot(visual)


DEFAULT_I2V_MOTION = (
    "中近景，摄影机缓慢向前推进；环境粒子与人物微动同步。"
    "禁止画面旋转、禁止甩镜、禁止扭曲变形、禁止万花筒、禁止分格。"
)

CINEMA_STYLE_BLOCK = (
    "真人电影质感，照片级真实，东方玄幻电影风格，细腻自然的人物皮肤与面部细节，"
    "真实摄影机实拍效果，浅景深，柔和电影光影，轻微胶片颗粒，真实空间纵深，高级电影调色。"
)

FREEDIDI_SHOT_RULES = (
    "【分镜规则】每镜仅对应本镜输入首帧，单镜全屏连续拍摄；"
    "不是多格同时发生；镜间剧情按镜号顺序推进，动作自然衔接，不要硬切贴纸感。"
    "【同场锁定】同一场景的连续镜必须共用同一空间母版与人物站位轴（左右关系固定）；"
    "对白时说话人朝向听话人，禁止正对镜头念白；段与段之间位置/朝向/陈设硬承接。"
)

NO_ONSCREEN_TEXT = (
    "【画面纯净】严禁任何 on-screen 文字：无字幕、无汉字、无字母、无数字标题、无水印、无 Logo、无 UI、"
    "无弹幕、无台词条、无说明文字、无烧录字幕、无书法牌匾可读字；"
    "台词只存在于音轨，画面中绝不对白上屏；clean cinematic frame, no subtitles, no captions。"
)


def _video_integration_suffix() -> str:
    from .generation_prefs import VIDEO_SCENE_INTEGRATION

    return VIDEO_SCENE_INTEGRATION

STILL_SUFFIX = "静态分镜剧照，单帧构图，几乎无运动。"


def _camera_from_motion(motion: str) -> str:
    m = _sanitize_motion(motion, [])
    if not m:
        return DEFAULT_I2V_MOTION
    tail = "摄影机运动平稳缓慢，不要突然甩镜或旋转。"
    if any(x in m for x in ("甩镜", "旋转", "禁止旋转")):
        return m if m.endswith("。") else m + "。"
    return m + "。" + tail


def _continuity_from_shot(shot: dict[str, Any], sid: int, scene_text: str = "") -> str:
    return _pick_continuity_text({**shot, "id": sid}, scene_text or _pick_scene_text(shot))


def _performance_from_shot(shot: dict[str, Any]) -> str:
    voices = _dedupe_voices(shot.get("voices") or [])
    base = _lip_motion_hint({"voices": voices})
    p = (shot.get("performance") or "").strip()
    if p:
        p = _strip_voice_text_from_prose(p, voices)
        if p and not _texts_equivalent(p, base):
            return p
    return (
        f"{base} "
        "轻微眨眼与呼吸，发丝/衣摆随环境微风自然摆动；"
        "保持五官稳定不换脸，不要夸张表情或肢体。"
    )


def _episode_preamble(ep: dict[str, Any], *, for_video: bool) -> str:
    if not for_video:
        return ""
    parts: List[str] = []
    syn = (ep.get("synopsis") or "").strip()
    if syn:
        parts.append(f"【整体故事】{syn}")
    bible = (ep.get("character_bible") or "").strip()
    if bible:
        parts.append(f"【人物一致性】{bible}")
    vstyle = (ep.get("visual_style") or ep.get("style_prefix") or CINEMA_STYLE_BLOCK).strip()
    parts.append(f"【整体风格】{vstyle}")
    parts.append(FREEDIDI_SHOT_RULES)
    parts.append(NO_ONSCREEN_TEXT)
    sync = ep.get("audio_sync") or {}
    lag = sync.get("mouth_lag_sec")
    if lag is not None:
        parts.append(
            f"【音画】镜内角色对白开口后约 {float(lag):.1f} 秒为配音落位参考（口型优先）；"
            "画外旁白仅音轨，画面人物口型禁止随旁白动。"
        )
    return "\n".join(parts) + "\n\n"


def shot_to_block(
    shot: dict[str, Any], style_prefix: str = "", *, for_video: bool = False
) -> str:
    sid = int(shot.get("id", 0))
    title = (shot.get("title") or f"镜{sid}").strip()
    visual = shot.get("visual") or ""
    sp = (shot.get("style_prefix") or style_prefix or "").strip()
    dur = int(shot.get("duration_sec") or 6)
    header = f"{sid:02d}｜{title}｜{dur}秒"
    motion = (shot.get("motion") or "").strip()
    voices = _dedupe_voices(shot.get("voices") or [])
    if for_video:
        scene = _prepend_style_once(sp, _pick_scene_text(shot))
        cam_raw = motion if not shot.get("camera") else str(shot.get("camera"))
        camera = _camera_from_motion(_sanitize_motion(cam_raw, voices))
        performance = _performance_from_shot({**shot, "voices": voices})
        continuity = _continuity_from_shot(shot, sid, scene)
        fusion = _video_integration_suffix()
        amb = (shot.get("ambience") or shot.get("sound") or "").strip()
        env_line = ""
        if amb:
            env_line = (
                f"\n【环境氛围】{amb}；镜头中须能看见/听感一致的环境细节"
                f"（如风雪、旗帜、人群、脚步回声等），与【摄影机】运动同步。"
            )
        lip_line = ""
        if _on_screen_dialogue_voices(voices):
            pass
        elif any(
            _is_narrator_role(v.get("role") or "") and (v.get("text") or "").strip()
            for v in (shot.get("voices") or [])
            if isinstance(v, dict)
        ):
            lip_line = f"\n【口型硬性】{_narrator_only_lip_hint()}"
        audio_dir = _h3_native_audio_directive(voices, amb)
        body = (
            f"镜头{sid}：\n"
            f"【参考首帧】严格以本镜输入分镜图（shot_{sid:02d}.png）为唯一构图与人物基准；"
            f"单镜全屏电影画面；禁止九宫格、禁止分格、禁止框线、禁止把输入图逐格播放。\n"
            f"【画面】{scene}\n"
            f"【摄影机】{camera}\n"
            f"【人物表演】{performance}\n"
            f"【剧情衔接】{continuity}"
            f"{env_line}{lip_line}\n"
            f"{audio_dir}\n"
            f"【硬性要求】{fusion}；地平线水平 upright；禁止旋转、甩镜、扭曲、万花筒。"
        )
        lines = [header, f"视频提示词：\n{body}"]
    else:
        prompt = _prepend_style_once(sp, visual)
        lines = [header, f"视频提示词：{prompt}。{STILL_SUFFIX}"]
    for v in voices:
        line = _fmt_voice_entry(v if isinstance(v, dict) else {})
        if line:
            lines.append(line)
    amb = (shot.get("ambience") or shot.get("sound") or "").strip()
    if amb:
        lines.append(f"声音：{amb}")
    return "\n".join(lines)


def episode_to_drama10(ep: dict[str, Any], *, for_video: bool = False) -> str:
    style = ep.get("style_prefix") or ""
    shots = ep.get("shots") or []
    if for_video:
        shots = [s for s in shots if not is_black_shot_visual(s.get("visual") or "")]
    blocks = [shot_to_block(s, style, for_video=for_video) for s in shots]
    return _episode_preamble(ep, for_video=for_video) + "\n\n".join(blocks)


def drama10_block_to_shot_fields(block: str) -> dict[str, Any]:
    """从单镜 Drama10 块还原镜头表字段（供 UI 展示，与底部整段预览互斥）。"""
    block = (block or "").strip()
    out: dict[str, Any] = {}
    hm = re.match(r"^\s*(\d{1,2})\s*[｜|]\s*([^｜|\n]+?)\s*[｜|]\s*(\d+(?:\.\d+)?)\s*秒", block)
    if hm:
        out["title"] = hm.group(2).strip()
        try:
            out["duration_sec"] = float(hm.group(3))
        except ValueError:
            pass

    def _sec(name: str) -> str:
        m = re.search(rf"【{name}】(.*?)(?=\n【|\n(?:旁白|对白|声音)\s*[：:]|\Z)", block, re.S)
        return (m.group(1).strip() if m else "")

    scene = _sec("画面")
    if scene:
        out["scene"] = scene
        out["visual"] = scene
    motion = _sec("摄影机")
    if motion:
        out["motion"] = motion
    perf = _sec("人物表演")
    if perf:
        out["performance"] = perf
    cont = _sec("剧情衔接")
    if cont:
        out["continuity"] = cont

    voices: list[dict[str, Any]] = []
    for m in re.finditer(
        r"(?:^|\n)\s*旁白\s*[：:]\s*「(.+?)」(?:\s*[（(]([\d.]+)\s*秒起?[）)])?",
        block,
        re.S,
    ):
        item: dict[str, Any] = {"role": "旁白", "text": m.group(1).strip()}
        if m.group(2):
            item["start_sec"] = float(m.group(2))
        voices.append(item)
    for m in re.finditer(
        r"(?:^|\n)\s*对白\s*[：:]\s*([^\n「]+?)「(.+?)」(?:\s*[（(]([\d.]+)\s*秒起?[）)])?",
        block,
        re.S,
    ):
        role = re.sub(r"\s+", "", m.group(1).strip().rstrip("：:"))
        item = {"role": role or "默认", "text": m.group(2).strip()}
        if m.group(3):
            item["start_sec"] = float(m.group(3))
        voices.append(item)
    if voices:
        out["voices"] = voices

    am = re.search(r"(?:^|\n)\s*声音\s*[：:]\s*(.+?)(?=\n\s*(?:旁白|对白|\d{2}\s*[｜|])|\Z)", block, re.S)
    if am:
        out["ambience"] = am.group(1).strip()[:200]
    return out


def merge_shots_from_drama10_prompt(
    shots: list[dict[str, Any]],
    prompt: str,
    *,
    only_fill_empty: bool = True,
) -> list[dict[str, Any]]:
    """把 Drama10 整段脚本拆回各镜字段；默认只补全镜头表里为空的项。"""
    blocks = extract_shot_blocks(prompt)
    if not blocks:
        return shots
    merged: list[dict[str, Any]] = []
    for s in shots:
        row = dict(s)
        sn = int(row.get("shot_num") or row.get("id") or 0)
        block = blocks.get(sn)
        if not block:
            merged.append(row)
            continue
        parsed = drama10_block_to_shot_fields(block)

        def _apply(key: str, val: Any) -> None:
            if val is None:
                return
            if key == "voices":
                if not val:
                    return
                cur = row.get("voices")
                if only_fill_empty and cur:
                    return
                row["voices"] = val
                return
            if key == "duration_sec":
                if only_fill_empty and row.get("duration_sec"):
                    return
                row["duration_sec"] = val
                return
            sval = (val if isinstance(val, str) else str(val)).strip()
            if not sval:
                return
            cur = (row.get(key) or "").strip()
            if only_fill_empty and cur:
                return
            row[key] = sval

        for k, v in parsed.items():
            _apply(k, v)
        merged.append(row)
    return merged


def extract_shot_blocks(prompt: str) -> dict[int, str]:
    """按「01｜」分镜头拆分为 {镜号: 块文本}。"""
    text = (prompt or "").strip()
    if not text:
        return {}
    parts = re.split(r"(?=(?:(?:^|\n)\d{2}｜))", text)
    out: dict[int, str] = {}
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(\d{2})｜", part)
        if not m:
            continue
        out[int(m.group(1))] = part
    return out


def filter_prompt_shots(prompt: str, shot_nums: List[int]) -> str:
    blocks = extract_shot_blocks(prompt)
    want = sorted(set(int(x) for x in shot_nums))
    picked = [blocks[n] for n in want if n in blocks]
    if not picked:
        raise ValueError(f"Prompt 中找不到镜头：{want}")
    return "\n\n".join(picked)


_NO_GRID_SUFFIX = "单镜头全屏画面，禁止分格拼图。"

LINGCHUAN_STILL_LOCK = (
    "全片唯一主角凌川：20多岁东方男性王子，黑长发，深色东方玄幻王族长礼服，"
    "写实电影脸；与本集所有凌川镜头必须是同一张脸、同一发型、同一套服装"
)


def enrich_still_prompt(visual: str, still: str, *, character_bible: str = "") -> str:
    """空镜去掉「误画主角」；有凌川时锁脸锁服装；竖屏/双人/手机屏纠偏。"""
    from .generation_prefs import (
        ESTABLISHING_STILL_HINT,
        ESTABLISHING_STILL_PREFIX,
        PHONE_SCREEN_HINT,
        TWO_SHOT_HEADROOM_HINT,
        VERTICAL_HINT,
        _is_establishing_shot,
        _is_vertical_visual,
        _needs_phone_screen_hint,
        _needs_two_shot_headroom,
    )

    vis = (visual or "").strip()
    out = (still or "").strip()
    out = re.sub(r"面对镜头(?:自然)?说话", "朝向对话对象说话", out)
    vis = re.sub(r"面对镜头(?:自然)?说话", "朝向对话对象说话", vis)

    if _is_establishing_shot(vis) or _is_establishing_shot(out):
        out = (
            f"{ESTABLISHING_STILL_PREFIX}；纯环境空镜，不要王子主角单人肖像；"
            f"{ESTABLISHING_STILL_HINT}，{out}"
        )
    combo = f"{vis}\n{out}"
    # Drama10 视频块常带「横屏16比9」，竖屏分镜静帧必须去掉，否则人物易被裁/朝向乱
    if _is_vertical_visual(combo):
        for bad, good in (
            ("横屏16比9构图", "竖屏9比16构图"),
            ("横屏16比9", "竖屏9比16"),
            ("横屏 16:9", "竖屏 9:16"),
            ("16:9 composition", "9:16 vertical composition"),
        ):
            out = out.replace(bad, good)
    prefix_bits: list[str] = []
    if _is_vertical_visual(combo) and VERTICAL_HINT not in out:
        prefix_bits.append(VERTICAL_HINT)
    if _needs_two_shot_headroom(combo) and TWO_SHOT_HEADROOM_HINT not in out:
        prefix_bits.append(TWO_SHOT_HEADROOM_HINT)
    if _needs_phone_screen_hint(combo) and PHONE_SCREEN_HINT not in out:
        prefix_bits.append(PHONE_SCREEN_HINT)
    # 对话戏静帧：锁朝向与同场空间，减少分段后换景/站位乱跳
    if re.search(r"(对白|说话|台词|朝向|对谈)", combo) and "禁止正对镜头" not in out:
        prefix_bits.append(
            "同场连续剧照：人物朝向对话对象而非镜头，左右站位与场景陈设稳定可衔接下一镜"
        )
    if prefix_bits:
        out = "；".join(prefix_bits) + "。" + out
    if "凌川" in vis or "Ling Chuan" in vis or "Lingchuan" in vis.lower():
        lock = LINGCHUAN_STILL_LOCK
        if (character_bible or "").strip():
            head = character_bible.strip().split("。")[0]
            if head:
                lock = f"{head}；全片凌川五官发型服装完全一致"
        out = f"{lock}。{out}"
    return out


def visual_prompt_from_shot_block(block: str) -> str:
    """从 Drama10 单镜块提取 Comfy 仍图用提示词（与 Drama10/SB 节点一致）。"""
    from .generation_prefs import (
        CROWD_WIDE_HINT,
        ESTABLISHING_STILL_HINT,
        FACE_SHOT_HINT,
        _is_crowd_wide_shot,
        _is_establishing_shot,
        _needs_face_hint,
    )

    vm = re.search(
        r"视频提示词\s*[：:]\s*(.*?)(?:\n\s*(?:旁白|对白|声音|神秘声音)\s*[：:]|\Z)",
        block,
        re.S,
    )
    body = (vm.group(1).strip() if vm else block.strip())
    if _is_establishing_shot(body):
        body = f"{ESTABLISHING_STILL_HINT}，{body}"
    elif _is_crowd_wide_shot(body):
        body = f"{CROWD_WIDE_HINT}，{body}"
    elif _needs_face_hint(body):
        body = body.replace("大特写", "中近景")
        body = f"{FACE_SHOT_HINT}，{body}"
    body = body + _NO_GRID_SUFFIX
    return f"静态分镜概念图，一张完整单镜头画面，{body}"


def still_prompt_for_shot(
    full_drama10: str,
    shot_num: int,
    *,
    visual: str = "",
    character_bible: str = "",
) -> str:
    block = filter_prompt_shots(full_drama10, [int(shot_num)])
    base = visual_prompt_from_shot_block(block)
    return enrich_still_prompt(visual, base, character_bible=character_bible)


def load_episode(ep_num: int) -> dict[str, Any]:
    path = DATA_DIR / f"ep{ep_num:02d}.json"
    if not path.is_file():
        raise FileNotFoundError(f"暂无 EP{ep_num:02d} 数据：{path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def list_episodes() -> List[int]:
    out = []
    for p in sorted(DATA_DIR.glob("ep*.json")):
        m = re.match(r"ep(\d+)\.json$", p.name)
        if m:
            out.append(int(m.group(1)))
    return out


def compute_grid(n: int) -> tuple[int, int]:
    if n <= 0:
        return 5, 2
    if n == 10:
        return 5, 2
    if n == 9:
        return 3, 3
    best = (n, 1)
    for r in range(1, n + 1):
        if n % r == 0:
            c = n // r
            if abs(c - 2 * r) < abs(best[0] - 2 * best[1]):
                best = (c, r)
    return best


def drama10_params(ep: dict[str, Any], width: int, height: int, shot_duration: Optional[float]) -> dict[str, Any]:
    shots = ep.get("shots") or []
    n = len(shots)
    cols, rows = compute_grid(n)
    if shot_duration is None:
        # Drama10 单参 shot_duration；取中位数并限制 2~15
        durs = [float(s.get("duration_sec", 6)) for s in shots]
        shot_duration = min(15.0, max(2.0, sorted(durs)[len(durs) // 2] if durs else 6.0))
    return {
        "width": width,
        "height": height,
        "shot_duration": shot_duration,
        "grid_cols": cols,
        "grid_rows": rows,
        "n_shots": n,
        "auto_storyboard": True,
        "turbo_mode": True,
        "steps": 8,
    }


def adapt_canvas_for_video(width: int, height: int) -> tuple[int, int]:
    """MiniMax H3 canvas; 短边默认 352（测试省显存；横屏 16:9 约 624×352）。"""
    import math
    import os

    canvas_multiple = 32
    base_short = int(os.environ.get("VIDEO_CANVAS_SHORT_EDGE", "352"))
    base_short = max(352, min(768, base_short))
    max_pixels = base_short * round(1344 * base_short / 768)
    ratio = width / height
    if ratio >= 1.0:
        nom_w, nom_h = base_short * ratio, float(base_short)
    else:
        nom_w, nom_h = float(base_short), base_short / ratio
    if nom_w * nom_h > max_pixels:
        s = math.sqrt(max_pixels / (nom_w * nom_h))
        nom_w, nom_h = nom_w * s, nom_h * s
    w = max(canvas_multiple, round(nom_w / canvas_multiple) * canvas_multiple)
    h = max(canvas_multiple, round(nom_h / canvas_multiple) * canvas_multiple)
    return int(w), int(h)
