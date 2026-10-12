# -*- coding: utf-8 -*-
"""全剧/分集脚本：OhMyGPT OpenAI 兼容 /v1/chat/completions。

推荐模型（中文长剧本 + 结构化 JSON）：
- gpt-6-astra（OpenAI 最新旗舰，默认）
- gpt-6.1-sol（近旗舰、更省）
- deepseek-chat（DeepSeek V3，中文性价比）
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .drama10_export import DATA_DIR
from .studio_settings import DEFAULT_OHMYGPT_API_BASE, ohmygpt_api_base, ohmygpt_api_key
from .voice_cast import DEFAULT_VOICE_CAST, enrich_episode_shots, merge_voice_cast, normalize_cast

DEFAULT_SCRIPT_MODEL = os.environ.get("OHMYGPT_SCRIPT_MODEL", "gpt-6-astra")

RECOMMENDED_SCRIPT_MODELS: List[Dict[str, str]] = [
    {"id": "gpt-6-astra", "label": "GPT-6 Astra（最新旗舰，推荐）"},
    {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol（近旗舰、更省）"},
    {"id": "gpt-6-luna", "label": "GPT-6 Luna（高吞吐）"},
    {"id": "deepseek-chat", "label": "DeepSeek V3（中文性价比）"},
    {"id": "gpt-4o", "label": "GPT-4o（旧）"},
    {"id": "gpt-4o-mini", "label": "GPT-4o mini（旧·省 token）"},
]

SERIES_RULES = """
《冰境》东方玄幻冰雪史诗短剧：20 集 × 每集约 60 秒 × 固定 10 个镜头。
主角：凌川（20 多岁东方王子，黑长发，深色王族礼服，全剧同一写实电影脸）。
反派/神秘线：神秘黑影（裂缝中人形轮廓，无可见五官）。
世界观：冰雪王国、黑色雪花、黑色神殿、天际黑色裂缝；梦境与现实交织。
禁止出现《爵迹》刃雪城、樱空释、麒零等无关 IP 人物或地名。
横屏 16:9，真人写实电影摄影，画面无字幕无 Logo。

制作约束（写入每镜 performance / motion）：
1. 镜 1 必须是环境定场空镜：visual 与 motion 明确 NO people NO hero NO prince；仅旁白画外音，人物口型不动。
2. 旁白均为画外音：含旁白对白时 performance 写「旁白画外，出镜人物嘴唇闭合不口型」。
3. motion 字段必须整段英文，供 MiniMax H3 图生视频；含 Ling Chuan 时写 SAME face and costume as series。
4. 每集恰好 10 镜，id 为 1–10；duration_sec 合计约 55–65 秒。
5. voices：有台词写 text + start_sec；无台词写 text 为空并加 note；环境镜 role 可为「环境」。
6. voice_cast 固定三角色：旁白 onyx、凌川 echo、神秘黑影 onyx（与全剧一致，可原样复制）。
7. style_prefix / visual_style / audio_sync 每集都要有；audio_sync 默认 mouth_lag_sec 0.6, drift_per_shot_sec 0.12。
""".strip()

EP01_PLOT_ANCHOR = """
第 1 集定调参考（可润色但需保留节拍）：《黑色裂缝》— 冰境王城空镜 → 凌川于王宫 → 噩梦冰原 → 黑雪 → 黑神殿 → 天际裂缝 → 靠近 → 黑影低语「你终于……回来了」→ 惊醒 → 现实夜空裂缝「不……它真的来了。」
""".strip()


def script_model(explicit: Optional[str] = None) -> str:
    m = (explicit or os.environ.get("OHMYGPT_SCRIPT_MODEL") or DEFAULT_SCRIPT_MODEL).strip()
    return m or DEFAULT_SCRIPT_MODEL


def _credentials() -> Tuple[str, str]:
    key = (ohmygpt_api_key() or os.environ.get("OPENAI_API_KEY") or "").strip()
    base = (ohmygpt_api_base() or DEFAULT_OHMYGPT_API_BASE).rstrip("/")
    return base, key


def _is_gpt5_plus(model: str) -> bool:
    m = (model or "").lower()
    return bool(re.match(r"^gpt-([5-9]|\d{2,})", m) or m.startswith("o3") or m.startswith("o4"))


def chat_completion(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: float = 0.65,
    max_tokens: int = 16000,
    timeout: float = 600.0,
) -> str:
    base, key = _credentials()
    if not key:
        raise RuntimeError("未配置 OhMyGPT API Key（制作台 → 全局制作 → OhMyGPT）")
    url = f"{base}/chat/completions"
    model = script_model(model)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    # GPT-5/6 系常用 max_completion_tokens；部分推理模型不接受自定义 temperature
    if _is_gpt5_plus(model):
        payload["max_completion_tokens"] = max_tokens
    else:
        payload["temperature"] = temperature
        payload["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, json=payload, headers=headers)
            if r.status_code >= 400:
                raise RuntimeError(f"OhMyGPT chat HTTP {r.status_code}: {r.text[:800]}")
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = (msg.get("content") or "").strip()
            if not content:
                raise RuntimeError("OhMyGPT 返回空 content")
            return content
        except Exception as e:
            last_err = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(str(last_err or "chat 失败"))


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        return json.loads(m.group(0))
    raise ValueError("无法从模型输出解析 JSON 对象")


def generate_series_outline(
    *,
    episode_count: int = 20,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    sys = f"{SERIES_RULES}\n\n{EP01_PLOT_ANCHOR}\n\n输出仅一个 JSON 对象，不要 markdown。"
    user = (
        f"为《冰境》生成 {episode_count} 集大纲。字段：series_title, episode_count, episodes（数组）。"
        f"每项：episode（1–{episode_count}）, title（4–8 字集名）, logline（1–2 句）, "
        "continuity_note（与上一集衔接，第 1 集写「开篇」）。"
        "整体 20 集要有完整主线：裂缝扩大、凌川身世、黑影身份、冰雪王国存亡。"
    )
    content = chat_completion(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        model=model,
        temperature=0.7,
        max_tokens=8000,
    )
    outline = _extract_json_object(content)
    eps = outline.get("episodes") or []
    if len(eps) < episode_count:
        raise RuntimeError(f"大纲仅 {len(eps)} 集，需要 {episode_count}")
    return outline


def _episode_user_prompt(
    ep_num: int,
    outline_entry: Dict[str, Any],
    prev_synopsis: str,
) -> str:
    title = outline_entry.get("title") or f"第{ep_num}集"
    logline = outline_entry.get("logline") or ""
    cont = outline_entry.get("continuity_note") or ""
    example_shot = {
        "id": 1,
        "duration_sec": 6,
        "title": "镜标题",
        "scene_code": "SC-01",
        "visual": "横屏16比9。中文画面描述…",
        "scene": "同 visual，供场景一致",
        "continuity": "与上一镜衔接说明",
        "camera": "机位/焦段/运动中文",
        "lighting": "光位与色调",
        "still_prompt": "静态分镜 API 用完整中文+风格句",
        "still_negative": "禁止项",
        "motion": "English cinematic prompt for image-to-video, no text…",
        "performance": "中文表演/口型/旁白画外说明…",
        "ambience": "English ambience keywords",
        "sfx": "关键音效",
        "characters_in_shot": ["角色名"],
        "voices": [
            {
                "role": "旁白",
                "text": "对白",
                "start_sec": 0.8,
                "tts_voice": "onyx",
                "voice_desc": "…",
                "emotion": "…",
            }
        ],
    }
    return f"""
生成《冰境》第 {ep_num} 集完整制作 JSON（单对象，无 markdown）。

本集标题：{title}
本集 logline：{logline}
衔接：{cont}
上一集 synopsis（若无则忽略）：{prev_synopsis or "（无）"}

必须包含键：episode, title, script_md, storyboard_md, synopsis, character_bible, voice_cast,
characters（人物库 visual_prompt/tts）, scenes（场景库 code/name/visual_prompt）,
production, audio_design, style_prefix, visual_style, audio_sync, shots（长度 10，每镜含 still_prompt/still_negative）。

单镜 schema 示例：
{json.dumps(example_shot, ensure_ascii=False)}

storyboard_md 形如：「01简称 02简称 … 10简称」。
第 1 镜必须是空镜定场；最后一镜留悬念或集末钩子。
""".strip()


def generate_episode_script(
    ep_num: int,
    outline_entry: Dict[str, Any],
    *,
    prev_synopsis: str = "",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    sys = f"{SERIES_RULES}\n\n只输出一个合法 JSON 对象，不要解释、不要 ```。"
    user = _episode_user_prompt(ep_num, outline_entry, prev_synopsis)
    content = chat_completion(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        model=model,
        temperature=0.62,
        max_tokens=16000,
    )
    try:
        ep = _extract_json_object(content)
    except (json.JSONDecodeError, ValueError) as e:
        fix = chat_completion(
            [
                {"role": "system", "content": "只输出修复后的完整 JSON 对象。"},
                {
                    "role": "user",
                    "content": f"下列内容不是合法 JSON，请修复为《冰境》第 {ep_num} 集完整 ep JSON：\n{content[:12000]}",
                },
            ],
            model=model,
            temperature=0.2,
            max_tokens=16000,
        )
        ep = _extract_json_object(fix)
        if not ep:
            raise RuntimeError(f"EP{ep_num:02d} JSON 解析失败: {e}") from e
    return normalize_episode_payload(ep, ep_num)


def normalize_episode_payload(raw: Dict[str, Any], ep_num: int) -> Dict[str, Any]:
    ep = dict(raw)
    ep["episode"] = int(ep_num)
    shots_in = ep.get("shots") or []
    shots: List[Dict[str, Any]] = []
    for i, s in enumerate(shots_in[:10], start=1):
        if not isinstance(s, dict):
            continue
        sh = dict(s)
        sh["id"] = int(sh.get("id") or i)
        sh["duration_sec"] = float(sh.get("duration_sec") or 6)
        for key in ("title", "visual", "motion", "performance", "ambience"):
            sh[key] = str(sh.get(key) or "").strip()
        if not sh.get("voices"):
            sh["voices"] = []
        shots.append(sh)
    while len(shots) < 10:
        n = len(shots) + 1
        shots.append(
            {
                "id": n,
                "duration_sec": 6.0,
                "title": f"待补镜{n}",
                "visual": "横屏16比9。待补画面描述。",
                "motion": "Cinematic eastern fantasy ice kingdom, wide shot, slow camera, no text.",
                "performance": "待补表演说明。",
                "ambience": "Wind and snow ambience",
                "voices": [],
            }
        )
    ep["shots"] = shots[:10]
    for sid, sh in enumerate(ep["shots"], start=1):
        sh["id"] = sid

    cast = merge_voice_cast(DEFAULT_VOICE_CAST, ep.get("voice_cast"))
    ep["voice_cast"] = normalize_cast(cast) or list(DEFAULT_VOICE_CAST)
    ep = enrich_episode_shots(ep, ep["voice_cast"])

    if not ep.get("audio_sync"):
        ep["audio_sync"] = {"mouth_lag_sec": 0.6, "drift_per_shot_sec": 0.12}
    if not ep.get("style_prefix"):
        ep["style_prefix"] = (
            "东方玄幻史诗电影，冰雪世界，横屏16比9，真人写实电影摄影，"
            "体积光与暴雪，画面无文字无字幕无Logo，单镜全屏连续实拍感"
        )
    if not ep.get("visual_style"):
        ep["visual_style"] = (
            "Epic eastern fantasy cinema, realistic humans, ice and snow kingdom, "
            "dramatic lighting, 16:9 widescreen, no on-screen text"
        )
    _patch_establishing_shot_one(ep["shots"][0])
    return ep


def _patch_establishing_shot_one(shot: Dict[str, Any]) -> None:
    v = shot.get("visual") or ""
    if "无人物" not in v and "无主角" not in v:
        shot["visual"] = (
            "横屏16比9。史诗环境定场（无人物、无主角）："
            + v.lstrip("横屏16比9。").strip()
        )
    m = shot.get("motion") or ""
    if "NO people" not in m.upper():
        shot["motion"] = (
            "Epic cinematic fantasy establishing shot, NO people NO hero NO prince in frame, "
            + m
        )


def write_episode_json(ep: Dict[str, Any], *, data_dir: Optional[Path] = None) -> Path:
    data_dir = data_dir or DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    n = int(ep.get("episode") or 1)
    path = data_dir / f"ep{n:02d}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(ep, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def generate_and_save_episodes(
    from_ep: int,
    to_ep: int,
    *,
    outline: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> List[Path]:
    from_ep, to_ep = int(from_ep), int(to_ep)
    if from_ep < 1 or to_ep < from_ep:
        raise ValueError("invalid episode range")
    if outline is None:
        outline = generate_series_outline(episode_count=max(to_ep, 20), model=model)
    by_ep = {
        int(x.get("episode")): x
        for x in (outline.get("episodes") or [])
        if isinstance(x, dict) and x.get("episode")
    }
    paths: List[Path] = []
    prev_synopsis = ""
    for n in range(from_ep, to_ep + 1):
        entry = by_ep.get(n) or {"episode": n, "title": f"第{n}集", "logline": ""}
        ep = generate_episode_script(n, entry, prev_synopsis=prev_synopsis, model=model)
        paths.append(write_episode_json(ep, data_dir=data_dir))
        prev_synopsis = str(ep.get("synopsis") or "")
        time.sleep(0.5)
    return paths


def list_script_model_options() -> List[Dict[str, str]]:
    return list(RECOMMENDED_SCRIPT_MODELS)
