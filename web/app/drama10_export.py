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
    """对白朝向：默认可看向对话对象；需要时允许正脸对镜头说话。"""
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
            f"站位对谈轴：{a}与{b}左右关系稳定；说话时可看向对方，"
            f"也可在情绪点正脸对镜头说话；非说话者双唇紧闭。"
        )
    role = roles[0] if roles else "角色"
    return (
        f"{role}说话时可看向画内对象，也可正脸对镜头；"
        f"允许边走边说/轻动作说话；非说话者闭口。"
    )


def _same_scene_bridge(shot: dict[str, Any]) -> str:
    """同场相邻镜：场景与左右站位固定，只从上一镜结束姿态往前接。"""
    c = (shot.get("continuity") or "").strip()
    base = (
        "本镜与同场上一镜是同一段连续拍摄：场景陈设、光色、人物左右位置、服装发型完全固定，"
        "禁止换景、禁止左右对调、禁止改姿势重摆；只从上一镜结束姿态往前接一小步动作，机位可略近不可翻面。"
    )
    if c:
        return f"{base}衔接：{c[:160]}"
    return base


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


def _clean_spoken_text(text: str) -> str:
    """Only the quoted dialogue is spoken; strip role labels / prefixes outside quotes."""
    t = (text or "").strip()
    if not t:
        return ""
    quoted = re.findall(r"[「\"“『](.+?)[」\"”』]", t, flags=re.S)
    if quoted:
        # Prefer the longest quoted span (avoids tiny nested bits)
        return max((q.strip() for q in quoted), key=len)
    # Strip leading 「角色名：」 style prefixes when quotes were omitted
    t2 = re.sub(
        r"^[\u4e00-\u9fffA-Za-z0-9·\-_]{1,20}\s*[：:]\s*",
        "",
        t,
        count=1,
    ).strip()
    return t2 or t


def strip_voice_reference_samples(text: str) -> str:
    """去掉音色/脚本里的「参考声音/参考台词」样例。

    必须按行处理：禁止 DOTALL 跨行删除（曾误删 P00x 段头前换行，导致每集只剩1段）。
    """
    t = text or ""
    if not t.strip():
        return ""
    out_lines: List[str] = []
    for line in t.splitlines(keepends=True):
        s = line
        s = re.sub(
            r"[,，;；、]?\s*参考\s*[「\"“『][^」\"”』]{0,120}[」\"”』]\s*[。．.]?",
            "",
            s,
        )
        s = re.sub(
            r"[,，;；、]?\s*参考句\s*[：:][^\n。．.;；]{0,120}[。．.;；]?",
            "",
            s,
        )
        s = re.sub(
            r"[,，;；、]?\s*参考\s*[：:][^\n。．.;；]{0,80}[。．.;；]?",
            "",
            s,
        )
        s = re.sub(
            r"对白干净利落[，,]?如\s*[「『\"“][^」』\"”]{0,80}[」』\"”]\s*[。．.]?",
            "对白干净利落。",
            s,
        )
        s = re.sub(r"如\s*[「『][^」』]{1,60}[」』]", "", s)
        s = re.sub(r"[；;]\s*[；;]+", "；", s)
        s = re.sub(r"[，,]\s*[，,]+", "，", s)
        out_lines.append(s)
    return "".join(out_lines).strip(" ；;，,。．.")


def repair_storyboard_segment_breaks(text: str) -> str:
    """恢复被误删的 P00x 段头换行，以及常见 [风格]/[全局] 粘连。"""
    t = text or ""
    if not t.strip():
        return t
    t = re.sub(r"([^\n])\s*(P\d{3}｜)", r"\1\n\n\2", t)
    t = re.sub(r"(｜\d+镜)\s*(\[风格\])", r"\1\n\2", t)
    t = re.sub(r"(画幅：\d+:\d+)\s*(\[全局)", r"\1\n\2", t)
    t = re.sub(r"(\d+秒)\s*(\[风格\])", r"\1\n\2", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


def _sanitize_meta_no_speech_quotes(text: str) -> str:
    """元数据/音色说明：去掉参考样例，并把残留「」改成无口播引号。"""
    t = strip_voice_reference_samples(text or "")
    if not t:
        return ""
    # 角色设定里的「对白样例」极易被模型当成开场台词念出
    t = re.sub(
        r"对白干净利落[，,]?如\s*[「『\"“][^」』\"”]{0,80}[」』\"”]\s*[。．.]?",
        "对白干净利落。",
        t,
    )
    t = re.sub(r"如\s*[「『\"“][^」』\"”]{1,60}[」』\"”]", "", t)
    # 其余中文引号改弯角，避免被当成口播正文
    t = t.replace("「", "『").replace("」", "』")
    t = t.replace("“", "『").replace("”", "』")
    return t.strip(" ；;，,。．.")


def _estimate_speak_sec(text: str, cps: float = 5.0) -> float:
    n_chars = sum(1 for ch in (text or "") if re.match(r"[\u4e00-\u9fffA-Za-z0-9]", ch))
    return max(1.1, float(n_chars) / max(3.5, float(cps)))


def pack_voice_starts_front(
    voices: list,
    duration_sec: float,
    *,
    first_at: float = 0.5,
    gap_sec: float = 0.35,
    cps: float = 5.0,
) -> list:
    """强制把对白挤到段前半（旧行为）。优先改用 fit_voice_starts_to_duration。"""
    dur = max(3.0, float(duration_sec or 6))
    t = max(0.35, float(first_at))
    out: list = []
    for v in voices or []:
        if not isinstance(v, dict):
            continue
        vv = dict(v)
        text = _clean_spoken_text(vv.get("text") or "")
        if not text:
            continue
        vv["text"] = text
        vv["start_sec"] = round(min(t, max(0.35, dur - 1.2)), 2)
        speak = _estimate_speak_sec(text, cps=cps)
        t = min(dur - 0.6, vv["start_sec"] + speak + float(gap_sec))
        out.append(vv)
    return out


def fit_voice_starts_to_duration(
    voices: list,
    duration_sec: float,
    *,
    gap_sec: float = 0.35,
    cps: float = 5.0,
    min_first: float = 0.5,
    pack_front_if_untimed: bool = True,
) -> list:
    """保留分镜脚本里的开嗓时间；仅当说不完时整体前移，避免「末尾开嗓」被截断。

    不再无脑把台词压到 0.5s：前半应静场、后半才说话时必须尊重时间轴，
    否则模型会一开场就念，后面用胡言填满剩余时长。
    """
    dur = max(3.0, float(duration_sec or 6))
    cleaned: list = []
    for v in voices or []:
        if not isinstance(v, dict):
            continue
        vv = dict(v)
        text = _clean_spoken_text(vv.get("text") or "")
        if not text:
            continue
        vv["text"] = text
        try:
            st = float(vv.get("start_sec")) if vv.get("start_sec") is not None else None
        except (TypeError, ValueError):
            st = None
        vv["start_sec"] = st
        cleaned.append(vv)
    if not cleaned:
        return []

    untimed = all(
        v.get("start_sec") is None or float(v.get("start_sec") or 0) in (0.0, 0.8)
        for v in cleaned
    )
    if untimed and pack_front_if_untimed:
        return pack_voice_starts_front(
            cleaned, dur, first_at=min_first, gap_sec=gap_sec, cps=cps
        )

    # 有明确时间轴：按 start 排序，必要时整体平移使最后一句说完
    ordered = sorted(
        cleaned,
        key=lambda x: float(x.get("start_sec") if x.get("start_sec") is not None else min_first),
    )
    for v in ordered:
        st = float(v.get("start_sec") if v.get("start_sec") is not None else min_first)
        v["start_sec"] = round(max(min_first, min(st, dur - 1.2)), 2)

    last = ordered[-1]
    last_end = float(last["start_sec"]) + _estimate_speak_sec(last.get("text") or "", cps=cps)
    overrun = last_end - (dur - 0.35)
    if overrun > 0.05:
        shift = overrun
        for v in ordered:
            v["start_sec"] = round(
                max(min_first, float(v["start_sec"]) - shift),
                2,
            )
    # 消除重叠：后一句不得早于前一句说完
    cursor = min_first
    for v in ordered:
        st = max(float(v["start_sec"]), cursor)
        v["start_sec"] = round(min(st, dur - 1.2), 2)
        cursor = v["start_sec"] + _estimate_speak_sec(v.get("text") or "", cps=cps) + float(gap_sec)
    return ordered


def _dedupe_voices(voices: list) -> list:
    out: list = []
    seen: set[tuple] = set()
    for v in voices or []:
        if not isinstance(v, dict):
            continue
        role = (v.get("role") or "默认").strip()
        text = _clean_spoken_text(v.get("text") or "")
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
        # Preserve optional tone metadata for prompt formatting
        for k in ("note", "tts_voice", "voice_desc", "emotion", "asset_visual"):
            if v.get(k):
                item[k] = v.get(k)
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


def _collapse_repeated_visual(text: str, max_len: int = 900) -> str:
    """去掉「竖屏提示+画面+避免」被多次粘贴的重复段。"""
    t = (text or "").strip()
    if not t:
        return ""
    # 只保留第一段「避免：」之前的画面 + 第一段避免列表
    if "避免：" in t:
        head, rest = t.split("避免：", 1)
        neg = rest.split("避免：", 1)[0]
        # 若 head 自身已首尾重复，取到第一次明显循环点
        marker = "竖屏9比16全屏构图"
        if head.count(marker) >= 2:
            i2 = head.find(marker, head.find(marker) + 1)
            if i2 > 40:
                head = head[:i2].rstrip("，；; ")
        t = f"{head.strip('，；; ')}。避免：{neg.strip()}"
    parts = [p.strip(" 。；;") for p in re.split(r"[；;]", t) if p.strip(" 。；;")]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        key = re.sub(r"\s+", "", p)
        if len(key) < 4 or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return "；".join(out)[:max_len]


def _pick_scene_text(shot: dict[str, Any]) -> str:
    scene = (shot.get("scene") or "").strip()
    visual = (shot.get("visual") or "").strip()
    if not scene:
        return _collapse_repeated_visual(visual)
    if not visual or _texts_equivalent(scene, visual):
        return _collapse_repeated_visual(scene)
    if _norm_text(visual) in _norm_text(scene):
        return _collapse_repeated_visual(scene)
    if _norm_text(scene) in _norm_text(visual):
        return _collapse_repeated_visual(visual)
    return _collapse_repeated_visual(f"{visual} {scene}".strip())


def _collapse_repeated_sentences(text: str, max_len: int = 180) -> str:
    """去掉连续粘贴的同一句（如朝向提示被追加十余次）。"""
    t = (text or "").strip()
    if not t:
        return ""
    parts = [p.strip() for p in re.split(r"(?<=[。；;])\s*", t) if p.strip()]
    out: List[str] = []
    seen: set[str] = set()
    for p in parts:
        key = _norm_text(p)
        if len(key) < 4 or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return "".join(out)[:max_len]


def _pick_continuity_text(shot: dict[str, Any], scene_text: str) -> str:
    sid = int(shot.get("id") or shot.get("shot_num") or 0)
    c = _collapse_repeated_sentences((shot.get("continuity") or "").strip(), max_len=160)
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
            f"第{sid}镜与前后连续：站位轴/光色不变，只推进表演，禁止换景翻面。"
            if sid
            else "与前后镜连续：站位轴不变，禁止换景翻面。"
        )
    # 朝向提示只补一次，且不与 base 重复
    face = _dialogue_facing_hint(shot)
    if face and _norm_text(face) not in _norm_text(base):
        return _collapse_repeated_sentences(f"{base} {face}", max_len=200)
    return base[:200]


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
    cont = _collapse_repeated_sentences((out.get("continuity") or "").strip(), max_len=160)
    scene_eff = _pick_scene_text({**out, "scene": out.get("scene"), "visual": visual})
    if cont and _texts_equivalent(cont, scene_eff):
        out["continuity"] = ""
    else:
        out["continuity"] = cont
    if visual:
        out["visual"] = _collapse_repeated_visual(
            _collapse_repeated_sentences(visual, max_len=500), max_len=400
        )
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


def _fmt_voice_entry(v: dict[str, Any], *, for_video: bool = False) -> str:
    role = (v.get("role") or "默认").strip()
    text = (v.get("text") or "").strip()
    note = (v.get("note") or "").strip()
    # 成片提示：只保留本镜对白正文，绝不附带音色参考样例/外形长文（易诱发多声）
    if for_video:
        return _fmt_voice(role, text, v.get("start_sec"))
    tv = (v.get("tts_voice") or "").strip()
    vd = _sanitize_meta_no_speech_quotes(v.get("voice_desc") or "")
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
        av = _sanitize_meta_no_speech_quotes(v.get("asset_visual") or "")
        if av:
            bits.append(f"外形:{av[:60]}")
        tone = "（" + "；".join(bits) + "）"
    if not text:
        if note:
            return f"音色说明：{role}{tone} — {_sanitize_meta_no_speech_quotes(note)}"
        return ""
    line = _fmt_voice(role, text, v.get("start_sec"))
    return f"{line}{tone}" if tone else line


def _ambience_zh(ambience: str) -> str:
    """环境声说明统一中文，避免英文干扰口播。"""
    amb = _sanitize_meta_no_speech_quotes(ambience or "").strip()
    if not amb:
        return "轻柔室外环境声、微风，无电流底噪"
    # 常见英文底噪描述转中文
    low = amb.lower()
    if "outdoor" in low or "wind" in low or "room tone" in low or "ambience" in low:
        return "轻柔室外环境声、微风，无电流底噪"
    if re.search(r"[A-Za-z]{4,}", amb) and not re.search(r"[\u4e00-\u9fff]", amb):
        return "轻柔环境声，无人声，无电流底噪"
    return amb


def _speaker_voice_zh(role: str, voice_desc: str = "") -> str:
    """音色用中文描述；姓名只作不可读标签。优先用资产库 voice_desc。"""
    vd = _sanitize_meta_no_speech_quotes((voice_desc or "").strip())
    if vd:
        # 截短，避免把参考台词样例塞进成片提示
        return vd[:36]
    r = (role or "").strip()
    if any(k in r for k in ("怀远", "爹", "父", "男", "公子", "少爷")):
        return "成年男声，自然语速"
    if any(
        k in r
        for k in ("锦禾", "禾", "团团", "阿杏", "童", "娃", "小小", "苏小")
    ):
        return "幼童女声，细软，自然语速"
    if any(k in r for k in ("姑娘", "小姐", "夫人", "女")):
        return "年轻女声，自然语速"
    return "自然语速人声"


# 单镜时长：内容少可短至 3s，有对白按语速加长，上限 10s（禁止为凑时长拖慢/空声）
SHOT_DURATION_MIN = 3.0
SHOT_DURATION_MAX = 10.0
H3_STEPS_DEFAULT = 7
H3_CFG_DEFAULT = 3.2
H3_VIDEO_SIGMA_SHIFT = 12.0
H3_AUDIO_SIGMA_SHIFT = 3.0

# 整段不要僵住静默；对白停口后仍用环境、动作、语气填满，不新增人声。
NO_COLD_SCENE = (
    "禁止冷场：整段不要僵住、不要静默空白。"
    "对白前后用环境声（风声、脚步、衣料、空气底噪）、持续动作（走动、呼吸、视线、手势）"
    "和有语气的声线填满；对白说完立刻停口，不续念、不加人声，动作与环境声持续到镜头结束。"
)
NO_COLD_SCENE_EN = (
    "no dead air, no frozen silence; keep ambient sound and continuous action "
    "through the whole clip; vocal tone stays alive; after the line lips stop "
    "but motion and ambience continue; no extra speaker."
)

H3_NEGATIVE_PROMPT = (
    "多余人声，自言自语，乱说话，莫名其妙台词，叽里咕噜杂音，额外旁白，画外音，背景音乐，"
    "人物嘴部乱抖动，嘴唇无规则开合，多个人同时说话，发音错乱，噪声，电流杂音，"
    "面部扭曲，脸变形，跳脸，手部畸形，肢体崩坏，水印，文字, "
    "extra human voice, self-talking, random gibberish voice, extra dialogue, "
    "background voice, narration, voiceover, mouth moving without speaking, "
    "face distortion, face swap, deformed limbs, blurry, watermark, text"
)

LTX_NEGATIVE_PROMPT = (
    "男声，成年男声，低沉男声，粗嗓，成人男声旁白，第二个人说话，"
    "乱叫，喊叫，尖叫，怪叫，哭闹，哼唱，自造句，语气词堆叠，"
    "跳跃，蹦跳，奔跑，手舞足蹈，剧烈肢体动作，"
    "换脸，跳脸，成人脸，写实皮肤，真人实拍，半写实3D，游戏渲染，"
    "画风漂移，照片级，欧美卡通，"
    "巨型兔子，兔子特写，活体兔子抢戏，道具变形，"
    "镜头推拉环绕，快速运镜，画面抖动，"
    "male voice, deep male voice, adult male narrator, screaming, yelling, shouting, "
    "gibberish, hopping, jumping, running, photorealistic, live action, 3d realistic, "
    "style drift, face swap, extra speaker, watermark, text"
)


def _ltx_standard_video_prompt(
    shot: dict[str, Any],
    voices: list,
    ambience: str,
) -> tuple[str, str]:
    """LTX2.5 图生视频原生音频。

    重要：I2V 时少写场景正文（场景已在静帧里），多写锁脸/锁声/锁画风，
    否则易跑画风、放大道具、乱叫或串男声。
    """
    spoken = [
        v
        for v in _dedupe_voices(voices if isinstance(voices, list) else [])
        if isinstance(v, dict) and _clean_spoken_text(v.get("text") or "")
    ][:1]
    style = (shot.get("style_prefix") or shot.get("art_style") or "").strip()
    if style and not style.startswith("画风"):
        style = f"画风：{style}"
    if not style:
        style = "画风：国风萌娃卡通，大头小身，圆润五官，儿童向卡通插画"
    amb = (ambience or "").strip() or (
        "持续风声、脚步与衣料摩擦，对白结束后环境声不中断、音量不归零"
    )
    move = (shot.get("motion") or "").strip()
    # 对白镜：边走边说；说完后动作和环境声继续到片尾
    motion = (
        "可缓慢走动或轻动作说话，允许正脸；不狂奔不连跳；"
        "仅说话人口型开合，他人闭口。"
        "对白一结束人物继续走或做收势，风吹衣袂，禁止定格。"
    )
    if move:
        motion = f"{move[:180]}。{motion}"
    lock = (
        "严格贴合分镜静帧：同一人物五官发型服装、同一卡通萌娃画风与色调，"
        "禁止画风漂移，禁止写成真人；同场空间不变只随机位。"
        + _same_scene_bridge(shot)
    )
    if spoken:
        role = (spoken[0].get("role") or "").strip()
        text = _clean_spoken_text(spoken[0].get("text") or "")
        tone = _speaker_voice_zh(role, spoken[0].get("voice_desc") or "")
        if "女" not in tone and any(k in role for k in ("小小", "锦禾", "团团", "阿杏")):
            tone = "幼童女声，细软，自然语速"
        positive = (
            f"{style}。竖屏中近景。{lock}"
            f"{motion}"
            f"只由{role or '画面中说话人'}以{tone}念出这一句普通话，禁止任何第二人声，"
            f"禁止乱叫喊叫旁白哼唱。{NO_COLD_SCENE}"
            f"对白：「{text}」。"
            "only one speaker; clear mandarin; no second voice; no screaming; "
            "may walk slowly while talking; front-facing OK; "
            "keep exact same cartoon style and identity as the first frame; "
            f"{NO_COLD_SCENE_EN} soundscape: {amb}"
        )
    else:
        positive = (
            f"{style}。竖屏中近景单人。{lock}"
            f"{motion}双唇紧闭不说话，无男声无乱叫。{NO_COLD_SCENE}"
            "lips sealed, no speech, no male voice, "
            f"{NO_COLD_SCENE_EN} same cartoon style as first frame."
        )
    return positive.strip(), LTX_NEGATIVE_PROMPT


def clamp_shot_duration_standard(seconds: float) -> float:
    """公共规则：单镜头 3~10s；内容少用短时长，禁止 >10s。"""
    try:
        d = float(seconds or 5.0)
    except (TypeError, ValueError):
        d = 5.0
    return max(SHOT_DURATION_MIN, min(SHOT_DURATION_MAX, d))


def _h3_standard_video_prompt(
    shot: dict[str, Any],
    voices: list,
    ambience: str,
    *,
    duration_sec: float = 0.0,
) -> str:
    """按《模型使用标准》生成 H3 图生视频原生音频提示（英文段名 + 中英正文）。"""
    spoken = [
        v
        for v in _dedupe_voices(voices if isinstance(voices, list) else [])
        if isinstance(v, dict) and _clean_spoken_text(v.get("text") or "")
    ]
    # 单镜只允许 1 人开口；多人对白应在分镜表拆段
    if len(spoken) > 1:
        spoken = spoken[:1]
    scene = _video_visual_brief(shot, max_len=120)
    scene = re.sub(
        r"(开嗓[^；。]*[；。]?|开口[^；。]*[；。]?|台词[^；。]*[；。]?|"
        r"对白[^；。]*[；。]?|口型[^；。]*[；。]?|说话[^；。]*[；。]?|"
        r"人物尚未开口[^；。]*[；。]?|到点[^；。]*[；。]?|"
        r"按时间轴[^；。]*[；。]?)",
        "",
        scene,
    ).strip(" ；;，,")
    amb = _ambience_zh(ambience)
    _ = duration_sec  # 时长由节点 shot_duration 控制，提示内不写时间窗以免诱发抢念

    if not spoken:
        body = (
            "integrated_multimodal_description:\n"
            f"竖屏中近景，S1人物，可缓慢走动或轻动作，双唇紧闭不说话，轻微表情变化。"
            f"{NO_COLD_SCENE}"
            f"人物外形与分镜静帧完全一致，不换脸不变形。{scene}\n"
            "lips sealed, no speech, subtle expression, may walk slowly, "
            f"{NO_COLD_SCENE_EN} identity locked to first frame.\n"
            "\n"
            "overall_soundscape:\n"
            f"{amb}；没有任何人声、旁白、说话声。\n"
            "no human voice, no narration, only subtle ambient noise.\n"
            "\n"
            "non_diegetic_music: N/A"
        )
    else:
        text = _clean_spoken_text(spoken[0].get("text") or "")
        role = (spoken[0].get("role") or "角色").strip()
        tone = _speaker_voice_zh(role, spoken[0].get("voice_desc") or "")
        body = (
            "integrated_multimodal_description:\n"
            f"竖屏中近景，仅{role}一人开口，{tone}，面部稳定，嘴唇开合同步说话；"
            f"可边走边说或轻动作说话，允许正脸对镜头；其他人物双唇紧闭绝不说话。"
            f"人物外形服装发型与分镜静帧完全一致，不换脸不变形。{NO_COLD_SCENE}"
            f"人物对白：<d>[中文]{text}</d>\n"
            f"同场空间不变，只随机位/角度；缓慢自然动作。"
            f"{((shot.get('motion') or '').strip()[:180] + '。') if (shot.get('motion') or '').strip() else ''}"
            f"对白结束后继续走动、点头、风吹衣袂，直到最后一帧，禁止定格。"
            f"{_same_scene_bridge(shot)}{scene}\n"
            "only one character speaking, clear mandarin dialogue, no extra voice, "
            "listeners lips sealed, no off-screen narration, no whispering, "
            "no random murmurs, may walk while talking, front-facing OK, "
            f"{NO_COLD_SCENE_EN} identity locked to first frame, same scene space.\n"
            "\n"
            "overall_soundscape:\n"
            f"{amb}；对白结束后风声、脚步、衣料声继续，音轨不要突然变静音；"
            f"没有额外人声，没有自言自语，没有第二个人说话。\n"
            "continuous wind, footsteps and cloth rustle after the line, "
            "do not drop to silence, no extra voices, no second speaker.\n"
            "\n"
            "non_diegetic_music: N/A"
        )
    return (
        f"{body}\n"
        f"\n"
        f"negative_prompt:\n"
        f"{H3_NEGATIVE_PROMPT}"
    )


def _h3_native_audio_directive(
    voices: list, ambience: str, *, duration_sec: float = 0.0
) -> str:
    """兼容旧调用名 → 标准 H3 多模态提示。"""
    return _h3_standard_video_prompt(
        {}, voices, ambience, duration_sec=duration_sec
    )


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
    "【同场锁定】同一场景的连续镜必须共用同一空间母版与人物形象（服装发型五官一致）；"
    "段间只改变机位/景别/人物前后位置，禁止整场换景翻面；"
    "对白时可看向对方或正脸对镜头，可边走边说；同一时刻仅一人开口。"
    "禁止冷场：对白停口后用环境声、持续动作和语气填满整段，不僵住、不加人声。"
)

NO_ONSCREEN_TEXT = (
    "【画面纯净】严禁任何 on-screen 文字：无字幕、无汉字、无字母、无数字标题、无水印、无 Logo、无 UI、"
    "无弹幕、无台词条、无说明文字、无烧录字幕、无书法牌匾可读字；"
    "台词只存在于音轨，画面中绝不对白上屏；clean cinematic frame, no subtitles, no captions。"
)


def _video_integration_suffix(art_style: str = "") -> str:
    from .generation_prefs import video_integration_for_style

    return video_integration_for_style(art_style)

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
    # 成片提示必须极短：只保留本段出场角色，避免长名单/场景名诱发乱念
    role_names: List[str] = []
    for sh in ep.get("shots") or []:
        for v in sh.get("voices") or []:
            if isinstance(v, dict):
                rn = (v.get("role") or "").strip()
                if rn and rn not in role_names and not _is_narrator_role(rn):
                    role_names.append(rn)
    if role_names:
        tags = "、".join(f"「{n}」" for n in role_names[:4])
        parts.append(
            "【人物】外形以参考分镜图为准；角色标签："
            + tags
            + "。以上姓名仅为标签，禁止在音轨中念出。"
        )
    from .generation_prefs import project_art_style_guide

    raw_style = (ep.get("visual_style") or ep.get("style_prefix") or "").strip()
    vstyle = project_art_style_guide(raw_style) if raw_style else CINEMA_STYLE_BLOCK
    vstyle_one = re.split(r"[。\n]", vstyle)[0].strip()[:80] or "国风萌娃卡通"
    parts.append(f"【风格】{vstyle_one}；竖屏9比16；无字幕水印。")
    parts.append(
        "【成片规则】单镜连续拍；每镜仅1人开口；提示格式见 "
        "integrated_multimodal_description / overall_soundscape / non_diegetic_music。"
    )
    return "\n".join(parts) + "\n\n"


def _video_visual_brief(shot: dict[str, Any], *, max_len: int = 200) -> str:
    """成片画面只要短描述；去掉重复站位/禁令，避免淹没口播规则。"""
    raw = _collapse_repeated_visual(
        (shot.get("visual") or shot.get("scene") or "").strip(), max_len=max_len * 2
    )
    raw = _collapse_repeated_sentences(raw, max_len=max_len * 2)
    # 站位锁定整句只保留一次，并缩短
    if raw.count("同场站位锁定") > 1:
        first = raw.find("同场站位锁定")
        second = raw.find("同场站位锁定", first + 1)
        if second > 0:
            raw = raw[:second].rstrip("；;，, ")
    raw = re.sub(r"(?:避免|Avoid)\s*[：:].*$", "", raw, flags=re.S)
    raw = re.sub(r"竖屏9比16全屏构图[^；]{0,80}；?", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" ；;，,。")
    if len(raw) > max_len:
        raw = raw[: max_len - 1].rstrip() + "…"
    return raw or "按参考分镜图构图与人物继续表演。"


def shot_to_block(
    shot: dict[str, Any], style_prefix: str = "", *, for_video: bool = False
) -> str:
    sid = int(shot.get("id", 0))
    title = (shot.get("title") or f"镜{sid}").strip()
    visual = shot.get("visual") or ""
    sp = (shot.get("style_prefix") or style_prefix or "").strip()
    dur = int(round(clamp_shot_duration_standard(shot.get("duration_sec") or 9)))
    header = f"{sid:02d}｜{title}｜{dur}秒"
    motion = (shot.get("motion") or "").strip()
    voices = _dedupe_voices(shot.get("voices") or [])
    if for_video:
        amb = (shot.get("ambience") or shot.get("sound") or "").strip()
        use_ltx = False
        try:
            from .studio_settings import use_ltx25_video

            use_ltx = bool(use_ltx25_video())
        except Exception:
            use_ltx = False
        if use_ltx:
            pos, neg = _ltx_standard_video_prompt(shot, voices, amb)
            body = f"{pos}\n\nnegative_prompt:\n{neg}"
        else:
            body = _h3_standard_video_prompt(
                shot, voices, amb, duration_sec=float(dur)
            )
        lines = [header, f"视频提示词：\n{body}"]
    else:
        prompt = _prepend_style_once(sp, visual)
        lines = [header, f"视频提示词：{prompt}。{STILL_SUFFIX}"]
        for v in voices:
            line = _fmt_voice_entry(v if isinstance(v, dict) else {}, for_video=False)
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


def enrich_still_prompt(
    visual: str,
    still: str,
    *,
    character_bible: str = "",
    art_style: str = "",
    width: int = 0,
    height: int = 0,
) -> str:
    """空镜去掉「误画主角」；有凌川时锁脸锁服装；竖屏/双人/手机屏纠偏。"""
    from .generation_prefs import (
        ESTABLISHING_STILL_HINT,
        ESTABLISHING_STILL_PREFIX,
        PHONE_SCREEN_HINT,
        TWO_SHOT_HEADROOM_HINT,
        VERTICAL_HINT,
        project_art_style_guide,
        is_illustration_art_style,
        is_portrait_canvas,
        strip_conflicting_look_phrases,
        _is_establishing_shot,
        _is_vertical_visual,
        _needs_phone_screen_hint,
        _needs_two_shot_headroom,
    )

    vis = (visual or "").strip()
    out = (still or "").strip()
    out = re.sub(r"面对镜头(?:自然)?说话", "朝向对话对象说话", out)
    vis = re.sub(r"面对镜头(?:自然)?说话", "朝向对话对象说话", vis)
    out = strip_conflicting_look_phrases(out, art_style=art_style)
    vis = strip_conflicting_look_phrases(vis, art_style=art_style)

    if _is_establishing_shot(vis) or _is_establishing_shot(out):
        # 冰镜空镜前缀含「冰雪王城」，非冰镜/卡通剧勿硬套
        if "冰镜" in (character_bible or "") or "凌川" in vis:
            out = (
                f"{ESTABLISHING_STILL_PREFIX}；纯环境空镜，不要王子主角单人肖像；"
                f"{ESTABLISHING_STILL_HINT}，{out}"
            )
        else:
            out = f"纯环境空镜，无清晰人脸；{ESTABLISHING_STILL_HINT}，{out}"
    combo = f"{vis}\n{out}"
    portrait = is_portrait_canvas(width, height) or _is_vertical_visual(combo)
    # Drama10 视频块常带「横屏16比9」，竖屏分镜静帧必须去掉，否则人物易被裁/朝向乱
    if portrait:
        for bad, good in (
            ("横屏16比9构图", "竖屏9比16构图"),
            ("横屏16比9", "竖屏9比16"),
            ("横屏 16:9", "竖屏 9:16"),
            ("16:9 composition", "9:16 vertical composition"),
        ):
            out = out.replace(bad, good)
    prefix_bits: list[str] = []
    if portrait and VERTICAL_HINT not in out:
        prefix_bits.append(VERTICAL_HINT)
    if _needs_two_shot_headroom(combo) and TWO_SHOT_HEADROOM_HINT not in out:
        prefix_bits.append(TWO_SHOT_HEADROOM_HINT)
    if _needs_phone_screen_hint(combo) and PHONE_SCREEN_HINT not in out:
        prefix_bits.append(PHONE_SCREEN_HINT)
    # 对话戏静帧：锁同场空间与人物形象；允许正脸说话
    if re.search(r"(对白|说话|台词|朝向|对谈)", combo):
        prefix_bits.append(
            "同场连续剧照：与前后镜共用同一场景母版、同一左右站位、同一服装发型；"
            "本镜只比上一镜推进一小步动作或机位略近，禁止换景、禁止左右对调、禁止下跪换装重摆"
        )
    if prefix_bits:
        out = "；".join(prefix_bits) + "。" + out
    # 仅冰镜主角锁；卡通剧勿注入写实王子设定
    if not is_illustration_art_style(art_style) and (
        "凌川" in vis or "Ling Chuan" in vis or "Lingchuan" in vis.lower()
    ):
        face_lock = LINGCHUAN_STILL_LOCK
        if (character_bible or "").strip():
            head = character_bible.strip().split("。")[0]
            if head:
                face_lock = f"{head}；全片凌川五官发型服装完全一致"
        out = f"{face_lock}。{out}"
    # 本剧画风置顶（与资产设定同一套描述；避免重复叠加）
    guide = project_art_style_guide(art_style)
    if guide:
        out = strip_conflicting_look_phrases(out, art_style=art_style)
        if out.startswith("画风："):
            return out
        if guide in out:
            out = out.replace(guide, "").strip("；。， ")
        out = f"{guide}。{out}"
    return out


def visual_prompt_from_shot_block(
    block: str, *, art_style: str = "", width: int = 0, height: int = 0
) -> str:
    """从 Drama10 单镜块提取 Comfy 仍图用提示词（与 Drama10/SB 节点一致）。"""
    from .generation_prefs import (
        CROWD_WIDE_HINT,
        ESTABLISHING_STILL_HINT,
        FACE_SHOT_HINT,
        ILLUSTRATION_FACE_HINT,
        VERTICAL_HINT,
        project_art_style_guide,
        is_illustration_art_style,
        is_portrait_canvas,
        strip_conflicting_look_phrases,
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
    body = strip_conflicting_look_phrases(body, art_style=art_style)
    illustration = is_illustration_art_style(art_style)
    if _is_establishing_shot(body):
        body = f"{ESTABLISHING_STILL_HINT}，{body}"
    elif _is_crowd_wide_shot(body):
        body = f"{CROWD_WIDE_HINT}，{body}"
    elif _needs_face_hint(body):
        body = body.replace("大特写", "中近景")
        face = ILLUSTRATION_FACE_HINT if illustration else FACE_SHOT_HINT
        body = f"{face}，{body}"
    if is_portrait_canvas(width, height) and VERTICAL_HINT not in body:
        body = f"{VERTICAL_HINT}，{body}"
    body = body + _NO_GRID_SUFFIX
    guide = project_art_style_guide(art_style)
    head = f"{guide}，" if guide else ""
    return f"{head}静态分镜概念图，一张完整单镜头画面，{body}"


def still_prompt_for_shot(
    full_drama10: str,
    shot_num: int,
    *,
    visual: str = "",
    character_bible: str = "",
    art_style: str = "",
    width: int = 0,
    height: int = 0,
) -> str:
    block = filter_prompt_shots(full_drama10, [int(shot_num)])
    base = visual_prompt_from_shot_block(
        block, art_style=art_style, width=width, height=height
    )
    return enrich_still_prompt(
        visual,
        base,
        character_bible=character_bible,
        art_style=art_style,
        width=width,
        height=height,
    )


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
    """分镜拼图行列。Comfy Drama10ShotMiniMaxH3 限制 grid_cols/grid_rows 均 ∈ [1,10]。"""
    max_side = 10
    if n <= 0:
        return 5, 2
    if n == 10:
        return 5, 2
    if n == 9:
        return 3, 3
    best: Optional[tuple[int, int]] = None

    def _consider(c: int, r: int) -> None:
        nonlocal best
        if c < 1 or r < 1 or c > max_side or r > max_side:
            return
        cand = (c, r)
        if best is None or abs(c - 2 * r) < abs(best[0] - 2 * best[1]):
            best = cand

    # 允许空格：用 ceil 分行，保证列/行都不超过 10
    for r in range(1, min(n, max_side) + 1):
        c = (n + r - 1) // r
        _consider(c, r)
    for c in range(1, min(n, max_side) + 1):
        r = (n + c - 1) // c
        _consider(c, r)
    # 整除方案
    for r in range(1, n + 1):
        if n % r != 0:
            continue
        _consider(n // r, r)
    if best is not None:
        return best
    cols = min(max_side, max(1, n))
    rows = min(max_side, max(1, (n + cols - 1) // cols))
    return cols, rows


def drama10_params(ep: dict[str, Any], width: int, height: int, shot_duration: Optional[float]) -> dict[str, Any]:
    shots = ep.get("shots") or []
    n = len(shots)
    cols, rows = compute_grid(n)
    if shot_duration is None:
        # 单镜 3~10s；取中位数再钳制
        durs = [
            clamp_shot_duration_standard(s.get("duration_sec", 5)) for s in shots
        ]
        shot_duration = (
            sorted(durs)[len(durs) // 2] if durs else 5.0
        )
    else:
        shot_duration = clamp_shot_duration_standard(shot_duration)
    return {
        "width": width,
        "height": height,
        "shot_duration": shot_duration,
        "grid_cols": cols,
        "grid_rows": rows,
        "n_shots": n,
        "auto_storyboard": True,
        "turbo_mode": False,
        "steps": H3_STEPS_DEFAULT,
        "cfg": H3_CFG_DEFAULT,
        "shift_video": H3_VIDEO_SIGMA_SHIFT,
        "shift_audio": H3_AUDIO_SIGMA_SHIFT,
    }


def adapt_canvas_for_video(width: int, height: int) -> tuple[int, int]:
    """成片画布：默认对齐项目宽高（32 对齐）。

    仅当显式设置 VIDEO_CANVAS_SHORT_EDGE（如 352）时才压到短边省显存。
    """
    import math
    import os

    canvas_multiple = 32
    w0 = max(64, int(width or 640))
    h0 = max(64, int(height or 360))
    short_env = (os.environ.get("VIDEO_CANVAS_SHORT_EDGE") or "").strip().lower()
    # 默认 / 0 / match / project → 跟项目尺寸走
    if short_env in ("", "0", "match", "project"):
        w = max(canvas_multiple, int(round(w0 / canvas_multiple) * canvas_multiple))
        h = max(canvas_multiple, int(round(h0 / canvas_multiple) * canvas_multiple))
        return int(w), int(h)

    try:
        base_short = int(short_env)
    except ValueError:
        base_short = 352
    base_short = max(352, min(768, base_short))
    max_pixels = base_short * round(1344 * base_short / 768)
    ratio = w0 / float(h0)
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
