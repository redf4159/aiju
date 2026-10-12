# -*- coding: utf-8 -*-
"""漫剧工作台流水线：导入小说剧本 → 剧本脚本 → 角色/场景/道具 → 视频脚本。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from . import db, services
from .drama10_script_llm import chat_completion, script_model

EP_HEAD_RE = re.compile(
    r"^第\s*0*(\d+)\s*集\s*[｜|]\s*(.+?)\s*$", re.MULTILINE
)
SCENE_RE = re.compile(
    r"^(EP\d+-SC\d+)\s*[｜|]\s*(.+?)\s*$", re.MULTILINE
)
PEOPLE_RE = re.compile(r"^人物[：:]\s*(.+?)\s*$", re.MULTILINE)
DIALOGUE_RE = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9·]{1,16})(?:（[^）]*）|\([^)]*\))?[：:]\s*(.+)$"
)


def _word_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def split_novel_episodes(novel: str) -> List[Dict[str, Any]]:
    """按「第N集｜标题」切分剧本正文。"""
    text = (novel or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    matches = list(EP_HEAD_RE.finditer(text))
    if not matches:
        return [
            {
                "ep_num": 1,
                "title": "第001集",
                "script_md": text,
                "word_count": _word_count(text),
            }
        ]
    out: List[Dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        ep_num = int(m.group(1))
        title = (m.group(2) or "").strip()
        out.append(
            {
                "ep_num": ep_num,
                "title": title,
                "script_md": f"第{ep_num:03d}集｜{title}\n\n{body}".strip(),
                "word_count": _word_count(body),
            }
        )
    return out


def heuristic_script_summary(novel: str, episodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """无 LLM 时的梗概占位（从正文抽前几段）。"""
    head = (novel or "").strip()
    # 跳过版本/定位行，取前 400 字动作/对白作梗概
    lines = [
        ln.strip()
        for ln in head.splitlines()
        if ln.strip()
        and not ln.startswith("版本")
        and not ln.startswith("定位")
        and "SCRIPTSET" not in ln
        and not ln.startswith("《")
    ]
    synopsis_bits = []
    for ln in lines:
        if EP_HEAD_RE.match(ln) or SCENE_RE.match(ln) or ln.startswith("人物"):
            continue
        if ln.startswith("▲") or "：" in ln or ":" in ln:
            synopsis_bits.append(ln.lstrip("▲").strip())
        if sum(len(x) for x in synopsis_bits) > 280:
            break
    synopsis = "；".join(synopsis_bits[:8]) if synopsis_bits else "（请完善故事梗概）"
    return {
        "synopsis": synopsis[:600],
        "genres": ["短剧"],
        "tone": "紧凑",
        "era": "",
        "art_style": "半写实3D国漫",
        "aspect_ratio": "16:9",
        "mode": "默认版",
        "episode_count": len(episodes) or 1,
        "parse_status": "done",
    }


def llm_script_summary(
    novel: str,
    episodes: List[Dict[str, Any]],
    *,
    model: Optional[str] = None,
    art_style: str = "半写实3D国漫",
) -> Dict[str, Any]:
    sample = (novel or "")[:12000]
    ep_lines = "\n".join(
        f"- 第{e['ep_num']:03d}集｜{e['title']}（{e.get('word_count') or 0}字）"
        for e in episodes[:40]
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是短剧剧本策划。根据导入的剧本正文，输出严格 JSON（不要 markdown）："
                '{"synopsis":"故事梗概","genres":["题材"],"tone":"情感基调",'
                '"era":"时代背景","art_style":"画风"}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"画风偏好：{art_style}\n分集列表：\n{ep_lines}\n\n剧本正文：\n{sample}"
            ),
        },
    ]
    raw = chat_completion(messages, model=script_model(model), temperature=0.4, max_tokens=2000)
    data = _extract_json(raw) or {}
    base = heuristic_script_summary(novel, episodes)
    base.update(
        {
            "synopsis": (data.get("synopsis") or base["synopsis"])[:800],
            "genres": data.get("genres") or base["genres"],
            "tone": data.get("tone") or base["tone"],
            "era": data.get("era") or base["era"],
            "art_style": data.get("art_style") or art_style or base["art_style"],
            "parse_status": "done",
        }
    )
    return base


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", t)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def extract_assets_from_scripts(
    episodes: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """从分集剧本启发式抽取角色 / 场景 / 道具，并收集对白与动作上下文。"""
    chars: Dict[str, Dict[str, Any]] = {}
    scenes: Dict[str, Dict[str, Any]] = {}
    props: Dict[str, Dict[str, Any]] = {}
    prop_hints = (
        "竹匣",
        "账单",
        "血玉蚕",
        "帕子",
        "药瓶",
        "金疮药",
        "断绝书",
        "马车",
        "蛊王",
        "玉佩",
        "剑",
        "刀",
        "令牌",
        "信件",
        "钥匙",
        "灯笼",
        "酒杯",
        "手镯",
        "戒指",
        "印章",
        "账本",
        "毒药",
        "蛊虫",
        "白瓷瓶",
        "金疮药瓶",
        "贡品",
        "锦盒",
        "手帕",
        "账册",
    )

    def _char(name: str) -> Dict[str, Any]:
        return chars.setdefault(
            name,
            {
                "name": name,
                "role_note": "",
                "visual_prompt": "",
                "voice_desc": "",
                "episodes": [],
                "_lines": [],
                "_actions": [],
            },
        )

    for ep in episodes:
        ep_num = int(ep.get("ep_num") or 0)
        body = ep.get("script_md") or ""
        lines = body.splitlines()
        for m in PEOPLE_RE.finditer(body):
            for name in re.split(r"[、,，/]|与|和", m.group(1)):
                name = name.strip()
                if not name or len(name) > 16:
                    continue
                slot = _char(name)
                if ep_num and ep_num not in slot["episodes"]:
                    slot["episodes"].append(ep_num)
        for m in DIALOGUE_RE.finditer(body):
            name = m.group(1).strip()
            if name in ("OS", "画外音", "旁白", "字幕"):
                continue
            if len(name) > 16:
                continue
            slot = _char(name)
            if ep_num and ep_num not in slot["episodes"]:
                slot["episodes"].append(ep_num)
            text = (m.group(2) or "").strip()
            if text and len(slot["_lines"]) < 6:
                slot["_lines"].append(text[:80])
        for ln in lines:
            s = ln.strip()
            if s.startswith("▲"):
                act = s.lstrip("▲").strip()
                for name, slot in chars.items():
                    if name and name in act and len(slot["_actions"]) < 5:
                        slot["_actions"].append(act[:100])
        current_scene_key = ""
        for ln in lines:
            s = ln.strip()
            sm = SCENE_RE.match(s)
            if sm:
                code = sm.group(1).strip()
                name = sm.group(2).strip()
                current_scene_key = name or code
                slot = scenes.setdefault(
                    current_scene_key,
                    {
                        "code": code,
                        "name": name,
                        "visual_prompt": "",
                        "role_note": "",
                        "episodes": [],
                        "_actions": [],
                    },
                )
                if ep_num and ep_num not in slot["episodes"]:
                    slot["episodes"].append(ep_num)
                continue
            if current_scene_key and s.startswith("▲"):
                act = s.lstrip("▲").strip()
                scenes[current_scene_key].setdefault("_actions", [])
                if len(scenes[current_scene_key]["_actions"]) < 6:
                    scenes[current_scene_key]["_actions"].append(act[:120])
        for hint in prop_hints:
            if hint in body:
                slot = props.setdefault(
                    hint,
                    {
                        "name": hint,
                        "visual_prompt": "",
                        "role_note": "",
                        "episodes": [],
                        "_ctx": [],
                    },
                )
                if ep_num and ep_num not in slot["episodes"]:
                    slot["episodes"].append(ep_num)
                for ln in lines:
                    if hint in ln and len(slot["_ctx"]) < 4:
                        slot["_ctx"].append(ln.strip().lstrip("▲").strip()[:100])

    out = {
        "characters": list(chars.values()),
        "scenes": list(scenes.values()),
        "props": list(props.values()),
    }
    for kind in ("characters", "scenes", "props"):
        for it in out.get(kind) or []:
            it["episodes"] = sorted(it.get("episodes") or [])
    # 保留 _lines/_actions/_ctx 供后续补全与 LLM；落库前再清理
    return fill_asset_descriptions(out)


def _is_weak_text(text: str, name: str = "", *, min_len: int = 12) -> bool:
    """空、过短、与名称相同、或旧版套话模板 → 视为需重写。"""
    t = (text or "").strip()
    n = (name or "").strip()
    if not t:
        return True
    if n and t in {
        n,
        f"「{n}」",
        f"{n}。",
        f"场景「{n}」",
        f"关剧道具「{n}」",
        f"关键剧道具「{n}」",
        f"关剧物件：{n}",
        f"关键剧物件：{n}",
    }:
        return True
    if len(t) < min_len:
        return True
    if n and t.replace("「", "").replace("」", "").replace("。", "").strip() == n:
        return True
    # 旧套话：描述以「场景/道具+名称」起头，或音色以「姓名：」起头
    if t.startswith("场景「") or t.startswith("关剧道具「") or t.startswith("关键剧道具「"):
        return True
    if (t.startswith("关剧物件：") or t.startswith("关键剧物件：")) and "剧情作用" not in t and "可视" not in t:
        return True
    # 音色旧写法「姓名：…」且过短
    if n and (t.startswith(f"{n}：") or t.startswith(f"{n}:")) and len(t) < max(36, len(n) + 20):
        return True
    return False


def _infer_voice_profile(name: str, lines: List[str], actions: List[str]) -> str:
    """参考「文字音色」写法：音域+质感+习惯+情绪变化，不以名字开头。"""
    female = ("小姐", "姑娘", "夫人", "母亲", "姐姐", "妹妹", "娘", "珠", "氏", "亭", "若", "柔")
    male = ("少爷", "侯爷", "公子", "父亲", "兄", "弟", "王", "将军", "首领", "道成", "暗卫")
    group = ("宾客", "护院", "众人", "家丁", "丫鬟", "婆子", "侍卫", "群")
    blob = name + "。" + "。".join(actions[:2]) + "。" + "。".join(lines[:2])

    if any(h in name for h in group):
        return "群像成年男女声线，层次丰富；附和讥讽时带轻蔑，危机时转为惊慌求生。"
    if any(h in name for h in female) or name.endswith("氏"):
        base = "年轻女性中高音，清亮偏冷，咬字利落"
        if "珠" in name or "明珠" in name:
            base = "细软带鼻音的年轻女声，语速偏慢，尾音委屈"
        elif "氏" in name or "夫人" in name or "沈" in name:
            base = "中年女声中音，沉稳带压，训诫时冷硬"
        mood = "反讽时轻顿，警告时压低声线"
        if any(k in blob for k in ("哭", "泪", "求")):
            mood = "受压时发颤，转攻时忽然拔高"
        return f"{base}；{mood}。"
    if any(h in name for h in male) or any(k in blob for k in ("侯爷", "家主", "下令", "威")):
        if "道成" in name or "侯" in name:
            return "中年男性中低音，威严厚重，发怒时提高音量但保持清晰，带侯府家主的命令口吻。"
        if "首领" in name or "暗卫" in name:
            return "成年男性偏低沉，短促有力，汇报时克制。"
        return "成年男性中低音，厚重稳实，命令口吻清晰。"
    if lines:
        return (
            f"中性偏冷声线，语速平稳，咬字清楚；语气随剧情在克制与压迫间切换，参考「{lines[0][:28]}」。"
        )
    return "中性偏冷声线，语速平稳，咬字清楚；情绪不夸张，重点句略压低。"


def _scene_place_parts(name: str) -> Tuple[str, str, str]:
    parts = [p for p in re.split(r"[·•|｜\-—]", name or "") if p.strip()]
    inout = ""
    place = name
    tod = ""
    if parts:
        if parts[0] in ("内", "外", "内景", "外景"):
            inout = parts[0].replace("景", "")
            place = parts[1] if len(parts) > 1 else name
            tod = parts[2] if len(parts) > 2 else ""
        else:
            place = parts[0]
            tod = parts[1] if len(parts) > 1 else ""
    return inout, place, tod


def fill_asset_descriptions(
    assets: Dict[str, List[Dict[str, Any]]],
    *,
    art_style: str = "半写实3D国漫",
    force: bool = False,
) -> Dict[str, List[Dict[str, Any]]]:
    """用剧本上下文补全空白/弱描述。角色/场景/道具描述不得等于名称；音色不含「姓名：」前缀。

    visual_prompt 统一为「外表描述 + 风格描述 + 图片展示结构」三段式，供 16:9 多视图设定图。
    """
    from .asset_sheet_prompts import build_structured_prompt

    for c in assets.get("characters") or []:
        name = (c.get("name") or "角色").strip()
        lines = c.get("_lines") or []
        actions = c.get("_actions") or []
        eps = c.get("episodes") or []
        ep_txt = "、".join(f"第{int(x)}集" for x in eps[:6]) if eps else "多集"

        if force or _is_weak_text(c.get("role_note") or "", name, min_len=18):
            act = actions[0] if actions else ""
            line_hint = f"对白干净利落，如「{lines[0][:24]}」。" if lines else ""
            if act:
                c["role_note"] = (
                    f"{name}，出场于{ep_txt}。剧中可见：{act[:70]}。{line_hint}"
                    f"外貌与身份需与府邸/江湖语境一致，性格从行动中体现。"
                )[:420]
            else:
                c["role_note"] = (
                    f"{name}，出场于{ep_txt}的关剧人物。"
                    f"{line_hint or '参与府内权力与生死冲突，神态克制、动作明确。'}"
                    f"设定需可画出稳定形象，供多集复用。"
                )[:420]

        if force or _is_weak_text(c.get("visual_prompt") or "", name, min_len=20):
            act_hint = (actions[0] if actions else "").replace(name, "").strip("，。 ")
            appearance = (
                f"{name}，古装人物，{act_hint or '站姿沉稳干练'}；"
                f"五官清晰、服饰材质可读，身份气质稳定，供多集复用。"
            )
            c["visual_prompt"] = build_structured_prompt(
                kind="character",
                name=name,
                appearance=appearance,
                art_style=art_style,
            )

        if force or _is_weak_text(c.get("voice_desc") or "", name, min_len=16):
            c["voice_desc"] = _infer_voice_profile(name, lines, actions)[:240]

    for s in assets.get("scenes") or []:
        name = (s.get("name") or s.get("code") or "场景").strip()
        actions = s.get("_actions") or []
        inout, place, tod = _scene_place_parts(name)
        mood = (actions[0] if actions else "").strip()
        if force or _is_weak_text(s.get("role_note") or "", name, min_len=20):
            loc = place or name
            time_bit = f"{tod}时分，" if tod else ""
            space = "室内" if inout == "内" else ("户外" if inout == "外" else "空间")
            if mood:
                s["role_note"] = (
                    f"{loc}，{time_bit}{space}空镜。气氛承接剧情：{mood[:80]}。"
                    f"强调建筑纵深、光影与陈设，不出现可识别人脸。"
                )[:320]
            else:
                s["role_note"] = (
                    f"{loc}，{time_bit}{space}定场空镜。"
                    f"保留朝代府邸/街巷结构与材质细节，光影统一，供分镜锚定。"
                )[:280]
        if force or _is_weak_text(s.get("visual_prompt") or "", name, min_len=24):
            appearance = (
                (f"{inout}景，" if inout else "")
                + f"{place or name}"
                + (f"，{tod}" if tod else "")
                + "。"
                + (f"{mood}。" if mood else "")
                + "宽景空间，梁柱门廊与地面材质清晰，统一光影，无人物面部特写。"
            )
            s["visual_prompt"] = build_structured_prompt(
                kind="scene",
                name=name,
                appearance=appearance,
                art_style=art_style,
            )

    for p in assets.get("props") or []:
        name = (p.get("name") or "道具").strip()
        ctx = p.get("_ctx") or []
        hint = (ctx[0] if ctx else "").strip()
        if force or _is_weak_text(p.get("role_note") or "", name, min_len=16):
            if hint:
                p["role_note"] = (
                    f"关键剧信物「{name}」，物件材质与轮廓清晰。"
                    f"剧情作用：{hint[:90]}。"
                    f"需单独成图，磨损状态可辨，便于多状态引用。"
                )[:280]
            else:
                p["role_note"] = (
                    f"关键剧信物「{name}」，作为冲突中的可视物件；特写时轮廓与材质稳定，背景干净。"
                )[:220]
        if force or _is_weak_text(p.get("visual_prompt") or "", name, min_len=20):
            appearance = (
                f"{name}，"
                + (f"{hint[:60]}，" if hint else "")
                + "物件轮廓清楚，材质与磨损可读，便于多状态引用。"
            )
            p["visual_prompt"] = build_structured_prompt(
                kind="prop",
                name=name,
                appearance=appearance,
                art_style=art_style,
            )

    return assets



def llm_enrich_assets(
    novel: str,
    assets: Dict[str, List[Dict[str, Any]]],
    *,
    art_style: str = "半写实3D国漫",
    model: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """用 LLM 分批补角色描述 / 音色 / 道具场景说明。"""
    assets = fill_asset_descriptions(assets, art_style=art_style)
    excerpt = (novel or "")[:6000]

    def _merge_list(kind: str, extras: List[Dict[str, Any]]) -> None:
        by_name = {x.get("name"): x for x in extras if x.get("name")}
        for item in assets.get(kind) or []:
            extra = by_name.get(item.get("name"))
            if not extra:
                continue
            for f in ("role_note", "voice_desc", "visual_prompt"):
                val = (extra.get(f) or "").strip()
                if val:
                    item[f] = val

    # 角色分批，降低超时概率
    chars = list(assets.get("characters") or [])
    for i in range(0, len(chars), 8):
        batch = chars[i : i + 8]
        payload = {
            "art_style": art_style,
            "characters": [
                {
                    "name": c["name"],
                    "episodes": c.get("episodes") or [],
                    "draft_role_note": (c.get("role_note") or "")[:160],
                    "sample_lines": (c.get("_lines") or [])[:3],
                }
                for c in batch
            ],
            "novel_excerpt": excerpt,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是漫剧资产设计师。为每个角色输出更完整的设定 JSON："
                    '{"characters":[{"name":"","role_note":"身份外貌性格120字内勿只写姓名",'
                    '"voice_desc":"音域音质语速习惯40字内勿以姓名开头如：中年男性中低音，威严厚重…",'
                    '"visual_prompt":"必须含三行：角色外表描述：…\\n风格描述：半写实3D国漫…\\n图片展示结构：单张横向16:9组合图，左侧正背面全身（正面无头背面有头）+右侧正面胸像…"}]}'
                    "visual_prompt 要足够长以约束服饰材质与三视图；必须覆盖名单中每个 name，只输出 JSON。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            raw = chat_completion(
                messages,
                model=script_model(model),
                temperature=0.4,
                max_tokens=2500,
                timeout=75.0,
            )
            data = _extract_json(raw) or {}
            _merge_list("characters", data.get("characters") or [])
        except Exception:
            continue

    # 场景 + 道具一次
    payload2 = {
        "art_style": art_style,
        "scenes": [
            {
                "name": s.get("name"),
                "code": s.get("code") or "",
                "draft": (s.get("visual_prompt") or "")[:120],
            }
            for s in (assets.get("scenes") or [])[:20]
        ],
        "props": [
            {"name": p.get("name"), "draft": (p.get("visual_prompt") or "")[:80]}
            for p in (assets.get("props") or [])[:20]
        ],
        "novel_excerpt": excerpt[:4000],
    }
    messages2 = [
        {
            "role": "system",
                "content": (
                "你是漫剧场景道具设计师。输出 JSON："
                '{"scenes":[{"name":"","role_note":"场景叙事说明40-80字勿等于名称",'
                '"visual_prompt":"须含：场景外表描述：…\\n风格描述：半写实3D国漫…\\n图片展示结构：单张横向16:9三视图母景组合…"}],'
                '"props":[{"name":"","role_note":"资产说明40字内写材质用途勿等于名称",'
                '"visual_prompt":"须含：道具外表描述：…\\n风格描述：半写实3D国漫…\\n图片展示结构：单张横向16:9正背+细节特写…"}]}'
                "覆盖全部名单，只输出 JSON。role_note 绝不能与 name 相同。"
            ),
        },
        {"role": "user", "content": json.dumps(payload2, ensure_ascii=False)},
    ]
    try:
        raw2 = chat_completion(
            messages2,
            model=script_model(model),
            temperature=0.4,
            max_tokens=3000,
            timeout=75.0,
        )
        data2 = _extract_json(raw2) or {}
        _merge_list("scenes", data2.get("scenes") or [])
        _merge_list("props", data2.get("props") or [])
    except Exception:
        pass

    return fill_asset_descriptions(assets, art_style=art_style)


def _episode_logline(script_md: str, title: str = "") -> str:
    """从分集剧本抽一句梗概。"""
    body = (script_md or "").strip()
    acts = []
    dials = []
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("▲"):
            acts.append(s.lstrip("▲").strip())
        else:
            m = DIALOGUE_RE.match(s)
            if m:
                dials.append(f"{m.group(1)}：{(m.group(2) or '').strip()}")
        if len(acts) + len(dials) >= 6:
            break
    core = "；".join((acts[:2] + dials[:2])[:4])
    if not core:
        core = re.sub(r"\s+", " ", body)[:120]
    prefix = f"「{title}」" if title else ""
    return (prefix + core)[:180]


def _dialogue_char_count(text: str) -> int:
    """估算中文台词可说字数（去掉标点空白）。"""
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text or ""))


def _natural_speech_budget(duration_sec: float) -> int:
    """自然语速预算：约 3.2 字/秒，并预留起势/停顿。"""
    speak = max(4.0, float(duration_sec) - 3.0)
    return max(12, int(speak * 3.2))


def build_video_script_text(
    *,
    ep: Dict[str, Any],
    summary: Dict[str, Any],
    characters: List[Dict[str, Any]],
    scenes: List[Dict[str, Any]],
    props: List[Dict[str, Any]],
    segment_sec: int = 15,
) -> str:
    """按「场 → 分段」生成视频脚本。

    不再硬压整集 1 分钟：按台词量拆段，目标约 90–150 秒（最多约 10 段×15s）。
    每段 12–15 秒；单段台词控制在自然语速可说完的范围内。
    """
    art = summary.get("art_style") or "半写实3D国漫"
    ratio = summary.get("aspect_ratio") or "16:9"
    title = ep.get("title") or f"第{int(ep.get('ep_num') or 1):03d}集"
    body = (ep.get("script_md") or "").strip()
    target_sec = max(12, min(15, int(segment_sec or 15)))
    # 整集预算：自然叙事优先，约 1.5–2.5 分钟
    max_segments = 10  # 10×15s ≈ 150s
    min_segments = 4  # 内容少时也不至于过碎

    # 按场景块拆分（场）；忽略第一场标记之前的标题/空行
    scene_blocks: List[Tuple[str, List[str]]] = []
    cur_name: Optional[str] = None
    cur_lines: List[str] = []
    for ln in body.splitlines():
        s = ln.strip()
        sm = SCENE_RE.match(s)
        if sm:
            if cur_name is not None and cur_lines:
                scene_blocks.append((cur_name, cur_lines))
            cur_name = sm.group(2).strip() or sm.group(1).strip()
            cur_lines = []
            continue
        if cur_name is None:
            continue
        if s:
            cur_lines.append(s)
    if cur_name is not None and cur_lines:
        scene_blocks.append((cur_name, cur_lines))
    if not scene_blocks:
        fallback_lines = [
            ln.strip()
            for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith("第")
        ]
        scene_blocks = [("本集场景", fallback_lines or ["（空剧本）"])]

    def _anchor_desc(name: str, note: str, fallback: str) -> str:
        d = (note or "").strip()
        if not d or d == name or d == f"是{name}。" or d.startswith(f"是{name}"):
            return fallback
        return d[:96]

    def _lines_to_beats(lines: List[str], scene_name: str) -> List[Dict[str, str]]:
        beats: List[Dict[str, str]] = []
        for s in lines:
            if s.startswith("人物"):
                continue
            if s.startswith("▲"):
                beats.append({"type": "action", "text": s.lstrip("▲").strip()})
                continue
            m = DIALOGUE_RE.match(s)
            if m:
                beats.append(
                    {
                        "type": "dialogue",
                        "role": m.group(1).strip(),
                        "text": (m.group(2) or "").strip(),
                    }
                )
            elif s and not s.startswith("第") and "｜" not in s[:8]:
                beats.append({"type": "action", "text": s})
        if not beats:
            beats = [{"type": "action", "text": f"{scene_name}定场"}]
        return beats

    def _pack_beats(beats: List[Dict[str, str]], n_shots: int) -> List[Dict[str, str]]:
        """少合并台词：优先一段一镜一句，避免把多句挤进同一镜导致语速飙升。"""
        n_shots = max(2, min(4, n_shots))
        if not beats:
            return [
                {"type": "action", "text": "气氛延展，推进剧情"},
                {"type": "action", "text": "人物微表情与环境呼应"},
            ]
        # 台词/动作尽量原样保留；只在超出镜数时把相邻「纯动作」合并
        items = list(beats)
        while len(items) > n_shots:
            # 优先合并相邻动作；绝不把两句对白硬拼成一句长台词
            merge_at = None
            for i in range(len(items) - 1):
                if items[i]["type"] != "dialogue" and items[i + 1]["type"] != "dialogue":
                    merge_at = i
                    break
            if merge_at is None:
                # 镜数仍超：丢掉最弱的纯气氛动作，保住对白
                drop_i = next(
                    (i for i, b in enumerate(items) if b["type"] != "dialogue"),
                    None,
                )
                if drop_i is None:
                    break
                items.pop(drop_i)
                continue
            a, b = items[merge_at], items[merge_at + 1]
            items[merge_at] = {
                "type": "action",
                "text": "；".join(
                    t for t in [(a.get("text") or ""), (b.get("text") or "")] if t
                )[:80],
            }
            items.pop(merge_at + 1)
        while len(items) < 2:
            items.append({"type": "action", "text": "气氛延展，留白停顿，勿赶词"})
        # 单句台词过长时截断到本镜可说完的字数（自然语速）
        per_shot_budget = max(10, _natural_speech_budget(target_sec) // max(1, len(items)))
        out: List[Dict[str, str]] = []
        for b in items[:n_shots]:
            if b.get("type") == "dialogue":
                t = (b.get("text") or "").strip()
                if _dialogue_char_count(t) > per_shot_budget + 8:
                    # 按标点优先截断
                    cut = t
                    for sep in ("。", "！", "？", "；", "，"):
                        if sep in t:
                            parts = t.split(sep)
                            acc = ""
                            for p in parts:
                                cand = (acc + p + sep).strip()
                                if _dialogue_char_count(cand) > per_shot_budget:
                                    break
                                acc = cand
                            if acc:
                                cut = acc
                                break
                    if _dialogue_char_count(cut) > per_shot_budget + 8:
                        # 硬截到预算附近
                        chars = list(t)
                        keep = []
                        n = 0
                        for ch in chars:
                            if re.match(r"[\u4e00-\u9fffA-Za-z0-9]", ch):
                                n += 1
                            keep.append(ch)
                            if n >= per_shot_budget:
                                break
                        cut = "".join(keep).rstrip("，、； ") + "…"
                    out.append({**b, "text": cut})
                else:
                    out.append(b)
            else:
                out.append(b)
        return out

    def _segment_assets(beats: List[Dict[str, str]], scene_name: str) -> Tuple[List[str], List[str]]:
        blob = scene_name + "。" + "。".join(
            (b.get("role") or "") + (b.get("text") or "") for b in beats
        )
        anchors: List[str] = []
        for s in scenes:
            n = s.get("name") or s.get("code") or ""
            if not n:
                continue
            if n in blob or n == scene_name or any(
                p and p in blob for p in re.split(r"[·•]", n) if len(p) >= 2
            ):
                anchors.append(
                    f"@{n}："
                    + _anchor_desc(
                        n,
                        s.get("role_note") or s.get("visual_prompt") or "",
                        f"{n}场景母景，空镜定场，光影统一",
                    )
                )
        if not any(scene_name in a.split("：", 1)[0] for a in anchors):
            anchors.insert(
                0,
                f"@{scene_name}："
                + _anchor_desc(scene_name, "", f"{scene_name}场景母景，空镜定场，光影统一"),
            )
        for c in characters:
            n = c.get("name") or ""
            if n and n in blob:
                anchors.append(
                    f"@{n}："
                    + _anchor_desc(n, c.get("role_note") or "", f"{n}角色形象，五官清晰，服饰稳定")
                )
        for p in props:
            n = p.get("name") or ""
            if n and n in blob:
                anchors.append(
                    f"@{n}："
                    + _anchor_desc(n, p.get("role_note") or "", f"{n}关剧物件，材质轮廓清晰")
                )
        seen = set()
        uniq = []
        for a in anchors:
            key = a.split("：", 1)[0]
            if key in seen:
                continue
            seen.add(key)
            uniq.append(a)
        voices = []
        for c in characters:
            n = c.get("name") or ""
            if not n or n not in blob:
                continue
            vd = (c.get("voice_desc") or "").strip()
            if vd and vd != n and not vd.startswith(f"是{n}"):
                voices.append(f"🔊 @{n} · 音色：{vd}")
            else:
                voices.append(f"🔊 @{n} · 音色：中性偏冷声线，咬字清楚，情绪随剧情切换。")
        return uniq[:14], voices[:10]

    def _roles_in_beats(beats: List[Dict[str, str]]) -> List[str]:
        roles: List[str] = []
        for b in beats:
            if b.get("type") != "dialogue":
                continue
            r = (b.get("role") or "").strip()
            if r and r not in roles:
                roles.append(r)
        return roles

    def _partner_for(speaker: str, roles: List[str], fallback: str = "") -> str:
        others = [r for r in roles if r and r != speaker]
        if others:
            return others[0]
        return (fallback or "").strip()

    def _blocking_line(roles: List[str], scene_name: str) -> str:
        if len(roles) >= 2:
            a, b = roles[0], roles[1]
            return (
                f"同场站位锁定：{a}在画面左侧偏中，{b}在画面右侧偏中，"
                f"两人相距约一臂、互为对谈轴；背景固定为「{scene_name}」同一机位空间，"
                f"家具门窗相对左右关系全段不变。"
            )
        if len(roles) == 1:
            return (
                f"同场站位锁定：{roles[0]}位于画面中偏右或中轴，朝向对谈/事件方向；"
                f"背景固定「{scene_name}」同一空间与陈设，禁止换景。"
            )
        return f"同场空间锁定：始终在「{scene_name}」同一实景，光色陈设与前后段一致。"

    def _build_segment_script(
        *,
        p_num: int,
        scene_idx: int,
        seg_in_scene: int,
        scene_name: str,
        beats: List[Dict[str, str]],
        dur: int,
        scene_roles: Optional[List[str]] = None,
        prev_handoff: str = "",
        scene_master: str = "",
    ) -> Tuple[str, str]:
        packed = _pack_beats(beats, n_shots=3)
        dur = max(12, min(15, int(dur)))
        base = max(3, dur // len(packed))
        rem = dur - base * len(packed)
        durs = [base] * len(packed)
        for i in range(max(0, rem)):
            durs[i % len(durs)] += 1
        while sum(durs) > 15:
            for i in range(len(durs)):
                if durs[i] > 3 and sum(durs) > 15:
                    durs[i] -= 1
        dur = min(15, sum(durs))

        roles = list(scene_roles or [])
        for r in _roles_in_beats(packed):
            if r not in roles:
                roles.append(r)
        blocking = _blocking_line(roles, scene_name)
        master = (scene_master or "").strip() or (
            f"场景母版「{scene_name}」：同一房间/场地、同一时间段光影与色温、同一陈设与门窗朝向；"
            f"本场各分段必须像同一场戏连续拍，禁止每段换一套景。"
        )

        anchors, voices = _segment_assets(packed, scene_name)
        head = (
            f"[风格]\n{art}\n画幅：{ratio}\n\n"
            f"[全局光影氛围]\n{art}，本段「{title}·{scene_name}」与同场其它分段共用一套光影色温；"
            f"人物面部可读；无背景音乐。\n\n"
            f"[场景母版]\n{master}\n\n"
            f"[站位轴]\n{blocking}\n\n"
            f"[一致性锁定]\n"
            + ("\n".join(anchors) if anchors else "（暂无资产锚定）")
            + ("\n" + "\n".join(voices) if voices else "")
            + "\n\n[负面排除]\n无背景音乐；无额外字幕；无水印；"
            "禁止人物正对镜头念台词；禁止每段换场景；禁止人物左右站位对调乱跳。\n\n"
        )

        t0 = 0
        shot_blocks = []
        last_pos = (prev_handoff or "").strip() or blocking
        for si, b in enumerate(packed, start=1):
            d = durs[si - 1]
            t1 = t0 + d
            if b["type"] == "dialogue":
                speaker = (b.get("role") or "角色").strip()
                partner = _partner_for(speaker, roles)
                if partner:
                    face = (
                        f"{speaker}身体与脸朝向{partner}说话（眼线看向对方脸/肩），"
                        f"禁止正对镜头念白；{partner}在画内或画外承对接听姿态，神态随内容反应；"
                    )
                else:
                    face = (
                        f"{speaker}朝向场景内对谈/事件方向说话，禁止正对镜头念白；"
                    )
                visual = (
                    f"{face}口型从容不赶，神态与台词一致；"
                    f"场景与站位：{scene_name}，{blocking}"
                )
                line = f"{speaker}：“{b.get('text')}”"
                handoff = (
                    f"承接：{speaker}仍在原站位，脸朝{partner or '对谈方向'}；"
                    f"背景陈设与上一段同一「{scene_name}」，左右关系不变。"
                )
            else:
                visual = (
                    f"{scene_name}。{b.get('text') or ''}；"
                    f"保持同场站位与陈设：{blocking}"
                )
                line = "无。"
                handoff = (
                    f"承接：动作结束后人物回到/保持原站位轴；"
                    f"场景仍为「{scene_name}」，光色陈设不跳切。"
                )
            pos_line = last_pos if si == 1 and prev_handoff else blocking
            if si == 1 and prev_handoff:
                pos_line = (
                    f"段间硬承接：{prev_handoff} "
                    f"本镜只推进表演，不重摆站位、不换景、不翻转左右。"
                )
            shot_blocks.append(
                f"镜头{si}（{t0:02d}:00—{t1:02d}:00，{d}秒）\n"
                f"【时间标识】\n{t0:02d}:00–{t1:02d}:00\n"
                f"【运镜】\n中近景缓慢推进，保持人物面部与场景纵深可读，不越轴、不翻面。\n"
                f"【画面】\n{visual}\n"
                f"【位置承接】\n{pos_line}\n"
                f"【OS】\n无。\n"
                f"【画外音】\n无。\n"
                f"【音效】\n环境声轻微可闻；对白自然语速，句间留短停顿。\n"
                f"【台词】\n{line}"
            )
            last_pos = handoff
            t0 = t1

        header = (
            f"P{p_num:03d}｜{title}·{scene_name}｜场{scene_idx}段{seg_in_scene}"
            f"｜总时长：{dur}秒｜{len(packed)}镜"
        )
        return header + "\n\n" + head + "\n\n".join(shot_blocks), last_pos

    # 每场拆成节拍；按「自然语速预算」切段，避免一场塞满对白却仍只有 15 秒
    seg_budget = _natural_speech_budget(target_sec)  # ~38 字 / 15s
    plan: List[Tuple[int, int, str, List[Dict[str, str]]]] = []
    # (scene_idx, seg_in_scene, scene_name, beats)

    def _flush_chunk(
        scene_idx: int,
        seg_in_scene: int,
        scene_name: str,
        chunk: List[Dict[str, str]],
    ) -> None:
        if chunk:
            plan.append((scene_idx, seg_in_scene, scene_name, list(chunk)))

    for si, (scene_name, lines) in enumerate(scene_blocks, start=1):
        beats = _lines_to_beats(lines, scene_name)
        chunk: List[Dict[str, str]] = []
        chunk_chars = 0
        seg_k = 1
        for b in beats:
            add = (
                _dialogue_char_count(b.get("text") or "")
                if b.get("type") == "dialogue"
                else 0
            )
            # 已有内容且再加一句台词会超预算 → 先封段
            if chunk and b.get("type") == "dialogue" and chunk_chars + add > seg_budget:
                _flush_chunk(si, seg_k, scene_name, chunk)
                seg_k += 1
                chunk = []
                chunk_chars = 0
            # 单句本身超预算：独占一段（后续 _pack_beats 会截到可说完）
            if b.get("type") == "dialogue" and add > seg_budget and chunk:
                _flush_chunk(si, seg_k, scene_name, chunk)
                seg_k += 1
                chunk = []
                chunk_chars = 0
            chunk.append(b)
            chunk_chars += add
            # 一段里最多约 3 个节拍，防止动作+多句对白挤爆
            if len(chunk) >= 3 and chunk_chars >= max(12, seg_budget // 2):
                _flush_chunk(si, seg_k, scene_name, chunk)
                seg_k += 1
                chunk = []
                chunk_chars = 0
        _flush_chunk(si, seg_k, scene_name, chunk)

    if not plan:
        plan = [(1, 1, "本集场景", [{"type": "action", "text": "定场空镜"}])]

    # 场太少且几乎没台词时，略拆以保持最低叙事段数；有台词则宁多勿赶
    dialogue_heavy = any(
        any(b.get("type") == "dialogue" for b in beats) for _, _, _, beats in plan
    )
    if len(plan) < min_segments and plan and not dialogue_heavy:
        rich_i = max(range(len(plan)), key=lambda i: len(plan[i][3]))
        need = min(3, max(2, min_segments - len(plan) + 1))
        scene_idx, _, name, beats = plan[rich_i]
        size = max(1, (len(beats) + need - 1) // need)
        parts = [beats[j : j + size] for j in range(0, len(beats), size)][:need]
        new_plan: List[Tuple[int, int, str, List[Dict[str, str]]]] = []
        for i, item in enumerate(plan):
            if i != rich_i:
                new_plan.append(item)
                continue
            for k, part in enumerate(parts or [beats], start=1):
                new_plan.append((scene_idx, k, name, part or beats[:1]))
        plan = new_plan

    plan = plan[:max_segments]

    # 同场角色并集 → 全场共用站位轴；段间写入位置承接
    scene_role_map: Dict[int, List[str]] = {}
    scene_master_map: Dict[int, str] = {}
    for scene_idx, _seg_k, scene_name, beats in plan:
        bucket = scene_role_map.setdefault(scene_idx, [])
        for r in _roles_in_beats(beats):
            if r not in bucket:
                bucket.append(r)
        if scene_idx not in scene_master_map:
            scene_master_map[scene_idx] = (
                f"场景母版「{scene_name}」：同一场地、同一时段光影色温、同一陈设门窗朝向；"
                f"本场各 P 段必须像连续一场戏，禁止每段换景或重摆站位。"
            )

    segs: List[str] = []
    prev_scene_idx: Optional[int] = None
    prev_handoff = ""
    for p_num, (scene_idx, seg_in_scene, scene_name, beats) in enumerate(plan, start=1):
        if prev_scene_idx is not None and scene_idx != prev_scene_idx:
            prev_handoff = ""
        body, prev_handoff = _build_segment_script(
            p_num=p_num,
            scene_idx=scene_idx,
            seg_in_scene=seg_in_scene,
            scene_name=scene_name,
            beats=beats,
            dur=target_sec,
            scene_roles=scene_role_map.get(scene_idx) or [],
            prev_handoff=prev_handoff,
            scene_master=scene_master_map.get(scene_idx) or "",
        )
        segs.append(body)
        prev_scene_idx = scene_idx

    return "\n\n".join(segs)



def llm_video_script(
    *,
    ep: Dict[str, Any],
    summary: Dict[str, Any],
    characters: List[Dict[str, Any]],
    scenes: List[Dict[str, Any]],
    props: List[Dict[str, Any]],
    model: Optional[str] = None,
) -> str:
    draft = build_video_script_text(
        ep=ep, summary=summary, characters=characters, scenes=scenes, props=props
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是短剧视频脚本导演。把分集剧本改写成「视频脚本」："
                "含 [风格] [全局光影氛围] [场景母版] [站位轴] [一致性锁定]（用 @资产名）[负面排除]，"
                "按「场→分段」输出：每段 P00x｜标题·场景｜场N段M｜总时长：13-15秒（不得超过15秒）；"
                "每段必须自含上述块，再拆本段内镜头N（起止秒），"
                "含【运镜】【画面】【位置承接】【OS】【画外音】【音效】【台词】。"
                "只输出视频脚本文本，不要 JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"画风：{summary.get('art_style') or '半写实3D国漫'}\n"
                f"画幅：{summary.get('aspect_ratio') or '16:9'}\n"
                f"角色：{', '.join(c.get('name','') for c in characters[:20])}\n"
                f"场景：{', '.join(s.get('name','') for s in scenes[:15])}\n"
                f"道具：{', '.join(p.get('name','') for p in props[:15])}\n\n"
                "硬性规则：每一 P 段总时长 12–15 秒且不得超过 15 秒；"
                "整集总时长约 90–150 秒（约 6–10 段），勿硬压成 1 分钟；"
                "按台词量拆段：单段台词建议不超过约 35–40 个汉字，保证自然语速可说完；"
                "一句长对白宁可拆成多段，也不要在一段里堆多句对白；"
                "每段脚本自洽（含风格/锁定/本段镜头）。\n"
                "【衔接与朝向·必须遵守】\n"
                "1) 同一「场」下的连续 P 段必须共用同一[场景母版]与[站位轴]：同一房间/场地、同一光色、同一左右站位，禁止每段换景或人物左右对调。\n"
                "2) 对白时说话人必须朝向听话人（眼线看对方脸/肩），写明「甲朝乙」；严禁「面对镜头说话/正对镜头念白」。\n"
                "3) 每段首镜【位置承接】必须承接上一段末状态（人物站哪、脸朝哪、背景陈设）；只推进表演，不重摆。\n"
                "4) 【画面】写清人物左右位置与朝向，不要只写情绪形容词。\n\n"
                f"分集剧本：\n{(ep.get('script_md') or '')[:10000]}\n\n"
                f"草稿参考：\n{draft[:8000]}"
            ),
        },
    ]
    try:
        return chat_completion(
            messages, model=script_model(model), temperature=0.45, max_tokens=14000
        ).strip()
    except Exception:
        return draft


def import_novel_project(
    *,
    name: str,
    novel_text: str,
    art_style: str = "半写实3D国漫",
    aspect_ratio: str = "16:9",
    mode: str = "默认版",
    width: int = 1280,
    height: int = 720,
    description: str = "",
) -> Dict[str, Any]:
    text = (novel_text or "").strip()
    if not text:
        raise ValueError("剧本内容为空")
    if len(text) > 600_000:
        raise ValueError("剧本过长（建议不超过约 20 万字）")
    eps = split_novel_episodes(text)
    if not eps:
        raise ValueError("未能识别分集，请使用「第001集｜标题」格式")
    # 标题：优先取《书名》
    title_m = re.search(r"《([^》]+)》", text[:200])
    proj_name = (name or "").strip() or (
        f"《{title_m.group(1)}》" if title_m else f"漫剧_{eps[0].get('title') or '新项目'}"
    )
    if aspect_ratio.strip() == "9:16":
        width, height = 720, 1280
    elif aspect_ratio.strip() == "16:9":
        width, height = 1280, 720
    proj = services.create_project(
        name=proj_name[:80],
        description=description or mode,
        episode_count=len(eps),
        width=width,
        height=height,
        style_prefix=art_style,
    )
    pid = proj["id"]
    summary = heuristic_script_summary(text, eps)
    summary.update(
        {
            "art_style": art_style,
            "aspect_ratio": aspect_ratio,
            "mode": mode,
            "episode_count": len(eps),
            "parse_status": "imported",
            "workflow_step": "outline",
        }
    )
    # 写入分集正文
    with db.connect() as conn:
        for e in eps:
            conn.execute(
                """
                UPDATE episodes SET title=?, script_md=?, outline=?
                WHERE project_id=? AND ep_num=?
                """,
                (
                    e["title"][:120],
                    e["script_md"],
                    f"{e.get('word_count') or 0}字",
                    pid,
                    int(e["ep_num"]),
                ),
            )
    services.update_project_docs(
        pid,
        outline_md=_format_outline_md(summary, eps),
        series_scripts_note="SCRIPTSET 导入",
        novel_md=text,
        script_meta_json=summary,
    )
    return services.get_project(pid)


def _format_outline_md(summary: Dict[str, Any], episodes: List[Dict[str, Any]]) -> str:
    genres = summary.get("genres") or []
    if isinstance(genres, list):
        g = "、".join(str(x) for x in genres)
    else:
        g = str(genres)
    lines = [
        "故事梗概",
        summary.get("synopsis") or "",
        "",
        "题材类型",
        g,
        "",
        "情感基调",
        summary.get("tone") or "",
        "",
        "时代背景",
        summary.get("era") or "",
        "",
        "分集",
    ]
    for e in episodes:
        lines.append(
            f"第{int(e['ep_num']):03d}集｜{e.get('title') or ''} {e.get('word_count') or 0}字"
        )
    return "\n".join(lines)


def parse_project_script(
    project_id: str,
    *,
    use_llm: bool = True,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    proj = services.get_project(project_id)
    docs = proj.get("docs") or {}
    novel = (docs.get("novel_md") or "").strip()
    if not novel:
        # 回退：拼接各集剧本
        eps_rows = services.list_episodes(project_id)
        novel = "\n\n".join(
            (e.get("script_md") or "") for e in eps_rows if e.get("script_md")
        )
    if not novel.strip():
        raise ValueError("项目尚无导入剧本")
    episodes = split_novel_episodes(novel)
    meta = {}
    raw_meta = docs.get("script_meta_json") or "{}"
    if isinstance(raw_meta, str):
        try:
            meta = json.loads(raw_meta or "{}")
        except json.JSONDecodeError:
            meta = {}
    elif isinstance(raw_meta, dict):
        meta = dict(raw_meta)
    art = meta.get("art_style") or proj.get("style_prefix") or "半写实3D国漫"
    if use_llm:
        try:
            summary = llm_script_summary(novel, episodes, model=model, art_style=art)
        except Exception as e:
            summary = heuristic_script_summary(novel, episodes)
            summary["llm_error"] = str(e)[:200]
    else:
        summary = heuristic_script_summary(novel, episodes)
    summary["art_style"] = art
    summary["aspect_ratio"] = meta.get("aspect_ratio") or summary.get("aspect_ratio") or "16:9"
    summary["mode"] = meta.get("mode") or "默认版"
    summary["parse_status"] = "done"
    summary["workflow_step"] = "outline"
    # 同步分集（若集数变化则扩/截由用户重建；此处只更新已有集）
    with db.connect() as conn:
        for e in episodes:
            conn.execute(
                """
                UPDATE episodes SET title=?, script_md=?, outline=?
                WHERE project_id=? AND ep_num=?
                """,
                (
                    e["title"][:120],
                    e["script_md"],
                    f"{e.get('word_count') or 0}字",
                    project_id,
                    int(e["ep_num"]),
                ),
            )
    services.update_project_docs(
        project_id,
        outline_md=_format_outline_md(summary, episodes),
        novel_md=novel,
        script_meta_json=summary,
    )
    return {
        "summary": summary,
        "episodes": [
            {
                "ep_num": e["ep_num"],
                "title": e["title"],
                "word_count": e.get("word_count") or 0,
            }
            for e in episodes
        ],
        "project": services.get_project(project_id),
    }


def parse_project_assets(
    project_id: str,
    *,
    use_llm: bool = True,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    proj = services.get_project(project_id)
    docs = proj.get("docs") or {}
    novel = (docs.get("novel_md") or "").strip()
    eps = services.list_episodes(project_id)
    ep_payload = [
        {"ep_num": e["ep_num"], "title": e.get("title"), "script_md": e.get("script_md") or ""}
        for e in eps
    ]
    if not novel:
        novel = "\n\n".join(e["script_md"] for e in ep_payload if e["script_md"])
    assets = extract_assets_from_scripts(ep_payload)
    meta = {}
    raw_meta = docs.get("script_meta_json") or "{}"
    if isinstance(raw_meta, str):
        try:
            meta = json.loads(raw_meta or "{}")
        except json.JSONDecodeError:
            meta = {}
    elif isinstance(raw_meta, dict):
        meta = dict(raw_meta)
    art = meta.get("art_style") or proj.get("style_prefix") or "半写实3D国漫"
    llm_note = ""
    # 保留已有形象/音色样本（按名称）
    prev_chars = {c.get("name"): c for c in services.list_characters(project_id)}
    prev_scenes = {s.get("name"): s for s in services.list_scenes(project_id)}
    prev_props = {p.get("name"): p for p in services.list_props(project_id)}

    def _persist(assets_in: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
        with db.connect() as conn:
            conn.execute("DELETE FROM characters WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM scenes WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM props WHERE project_id=?", (project_id,))
        for i, c in enumerate(assets_in.get("characters") or []):
            old = prev_chars.get(c.get("name")) or {}
            services.upsert_character(
                project_id,
                {
                    "name": c["name"],
                    "role_note": c.get("role_note") or "",
                    "visual_prompt": c.get("visual_prompt") or "",
                    "voice_desc": c.get("voice_desc") or "",
                    "sort_order": i,
                    "episodes_json": c.get("episodes") or [],
                    "ref_image": old.get("ref_image") or "",
                    "ref_images": old.get("ref_images") or [],
                    "voice_samples": old.get("voice_samples") or [],
                    "tts_voice": old.get("tts_voice") or "",
                },
            )
        for i, s in enumerate(assets_in.get("scenes") or []):
            old = prev_scenes.get(s.get("name")) or {}
            services.upsert_scene(
                project_id,
                {
                    "code": s.get("code") or "",
                    "name": s.get("name") or s.get("code") or f"场景{i+1}",
                    "visual_prompt": s.get("visual_prompt") or "",
                    "role_note": s.get("role_note") or "",
                    "sort_order": i,
                    "episodes_json": s.get("episodes") or [],
                    "ref_image": old.get("ref_image") or "",
                    "ref_images": old.get("ref_images") or [],
                },
            )
        for i, p in enumerate(assets_in.get("props") or []):
            old = prev_props.get(p.get("name")) or {}
            services.upsert_prop(
                project_id,
                {
                    "name": p.get("name") or f"道具{i+1}",
                    "visual_prompt": p.get("visual_prompt") or "",
                    "role_note": p.get("role_note") or "",
                    "sort_order": i,
                    "episodes_json": p.get("episodes") or [],
                    "ref_image": old.get("ref_image") or "",
                    "ref_images": old.get("ref_images") or [],
                },
            )
        return {
            "characters": len(assets_in.get("characters") or []),
            "scenes": len(assets_in.get("scenes") or []),
            "props": len(assets_in.get("props") or []),
        }

    # 先用剧本上下文补全描述，保证落盘不为空
    assets = fill_asset_descriptions(assets, art_style=art)
    counts = _persist(assets)
    meta["assets_status"] = "done"
    meta["workflow_step"] = "assets"
    meta["asset_counts"] = counts
    meta["asset_enrich"] = "heuristic"
    services.update_project_docs(project_id, script_meta_json=meta)

    if use_llm:
        try:
            enriched = llm_enrich_assets(novel, assets, art_style=art, model=model)
            counts = _persist(enriched)
            assets = enriched
            meta["asset_counts"] = counts
            meta["asset_enrich"] = "llm"
            services.update_project_docs(project_id, script_meta_json=meta)
        except Exception as e:
            llm_note = str(e)[:200]
            meta["asset_llm_error"] = llm_note
            services.update_project_docs(project_id, script_meta_json=meta)

    return {
        "counts": counts,
        "characters": services.list_characters(project_id),
        "scenes": services.list_scenes(project_id),
        "props": services.list_props(project_id),
        "project": services.get_project(project_id),
        "llm_note": llm_note,
    }


def enrich_existing_asset_descriptions(
    project_id: str,
    *,
    use_llm: bool = True,
    model: Optional[str] = None,
    asset_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """不删资产，只补全空白/过短描述（用于已有项目一键修复）。asset_ids 指定时只处理这些资产。"""
    proj = services.get_project(project_id)
    docs = proj.get("docs") or {}
    novel = (docs.get("novel_md") or "").strip()
    eps = services.list_episodes(project_id)
    ep_payload = [
        {"ep_num": e["ep_num"], "title": e.get("title"), "script_md": e.get("script_md") or ""}
        for e in eps
    ]
    if not novel:
        novel = "\n\n".join(e["script_md"] for e in ep_payload if e["script_md"])
    extracted = extract_assets_from_scripts(ep_payload)
    by_name = {
        "characters": {c["name"]: c for c in extracted.get("characters") or []},
        "scenes": {s["name"]: s for s in extracted.get("scenes") or []},
        "props": {p["name"]: p for p in extracted.get("props") or []},
    }
    meta = {}
    raw_meta = docs.get("script_meta_json") or "{}"
    if isinstance(raw_meta, str):
        try:
            meta = json.loads(raw_meta or "{}")
        except json.JSONDecodeError:
            meta = {}
    elif isinstance(raw_meta, dict):
        meta = dict(raw_meta)
    art = meta.get("art_style") or proj.get("style_prefix") or "半写实3D国漫"
    id_set = {str(x).strip() for x in (asset_ids or []) if str(x).strip()}

    chars = services.list_characters(project_id)
    scenes = services.list_scenes(project_id)
    props = services.list_props(project_id)
    if id_set:
        chars = [c for c in chars if c.get("id") in id_set]
        scenes = [s for s in scenes if s.get("id") in id_set]
        props = [p for p in props if p.get("id") in id_set]

    # 合并抽取上下文到现有资产；弱描述清空后由 fill 重写
    merged_chars = []
    for c in chars:
        ex = by_name["characters"].get(c.get("name")) or {}
        name = c.get("name") or ""
        item = {
            **c,
            "_lines": ex.get("_lines") or [],
            "_actions": ex.get("_actions") or [],
            "episodes": c.get("episodes") or ex.get("episodes") or [],
        }
        if _is_weak_text(item.get("role_note") or "", name, min_len=18):
            item["role_note"] = ""
        if _is_weak_text(item.get("visual_prompt") or "", name, min_len=20):
            item["visual_prompt"] = ""
        if _is_weak_text(item.get("voice_desc") or "", name, min_len=16):
            item["voice_desc"] = ""
        merged_chars.append(item)
    merged_scenes = []
    for s in scenes:
        ex = by_name["scenes"].get(s.get("name")) or {}
        name = s.get("name") or ""
        item = {
            **s,
            "_actions": ex.get("_actions") or [],
            "episodes": s.get("episodes") or ex.get("episodes") or [],
        }
        if _is_weak_text(item.get("visual_prompt") or "", name, min_len=24):
            item["visual_prompt"] = ""
        if _is_weak_text(item.get("role_note") or "", name, min_len=20):
            item["role_note"] = ""
        merged_scenes.append(item)
    merged_props = []
    for p in props:
        ex = by_name["props"].get(p.get("name")) or {}
        name = p.get("name") or ""
        item = {
            **p,
            "_ctx": ex.get("_ctx") or [],
            "episodes": p.get("episodes") or ex.get("episodes") or [],
        }
        if _is_weak_text(item.get("visual_prompt") or "", name, min_len=20):
            item["visual_prompt"] = ""
        if _is_weak_text(item.get("role_note") or "", name, min_len=16):
            item["role_note"] = ""
        merged_props.append(item)

    pack = {
        "characters": merged_chars,
        "scenes": merged_scenes,
        "props": merged_props,
    }
    pack = fill_asset_descriptions(pack, art_style=art)
    if use_llm:
        try:
            pack = llm_enrich_assets(novel, pack, art_style=art, model=model)
        except Exception as e:
            meta["asset_llm_error"] = str(e)[:200]

    for c in pack.get("characters") or []:
        services.upsert_character(
            project_id,
            {
                "id": c.get("id"),
                "name": c["name"],
                "role_note": c.get("role_note") or "",
                "visual_prompt": c.get("visual_prompt") or "",
                "voice_desc": c.get("voice_desc") or "",
                "episodes": c.get("episodes") or [],
                "ref_image": c.get("ref_image") or "",
                "ref_images": c.get("ref_images") or [],
                "voice_samples": c.get("voice_samples") or [],
                "tts_voice": c.get("tts_voice") or "",
                "sort_order": int(c.get("sort_order") or 0),
            },
        )
    for s in pack.get("scenes") or []:
        services.upsert_scene(
            project_id,
            {
                "id": s.get("id"),
                "code": s.get("code") or "",
                "name": s.get("name") or "",
                "visual_prompt": s.get("visual_prompt") or "",
                "role_note": s.get("role_note") or "",
                "episodes": s.get("episodes") or [],
                "ref_image": s.get("ref_image") or "",
                "ref_images": s.get("ref_images") or [],
                "sort_order": int(s.get("sort_order") or 0),
            },
        )
    for p in pack.get("props") or []:
        services.upsert_prop(
            project_id,
            {
                "id": p.get("id"),
                "name": p.get("name") or "",
                "visual_prompt": p.get("visual_prompt") or "",
                "role_note": p.get("role_note") or "",
                "episodes": p.get("episodes") or [],
                "ref_image": p.get("ref_image") or "",
                "ref_images": p.get("ref_images") or [],
                "sort_order": int(p.get("sort_order") or 0),
            },
        )
    meta["asset_enrich"] = "llm" if use_llm else "heuristic"
    meta["assets_status"] = "done"
    services.update_project_docs(project_id, script_meta_json=meta)
    return {
        "characters": services.list_characters(project_id),
        "scenes": services.list_scenes(project_id),
        "props": services.list_props(project_id),
        "project": services.get_project(project_id),
    }


def fill_episode_outlines(project_id: str) -> int:
    """补全分集梗概（outline 字段），避免只有「字数」。"""
    n = 0
    for ep in services.list_episodes(project_id):
        outline = (ep.get("outline") or "").strip()
        script = ep.get("script_md") or ""
        only_wc = bool(re.fullmatch(r"\d+字", outline)) or not outline
        if only_wc and script.strip():
            logline = _episode_logline(script, ep.get("title") or "")
            wc = _word_count(script)
            services.update_episode(ep["id"], outline=f"{logline}（{wc}字）")
            n += 1
    return n


def complete_project_content(
    project_id: str,
    *,
    use_llm: bool = True,
    model: Optional[str] = None,
    include_assets: bool = True,
    include_outline: bool = True,
    include_video_scripts: bool = True,
    only_missing_scripts: bool = True,
    script_limit: int = 20,
    asset_ids: Optional[List[str]] = None,
    episode_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """一键补全：资产描述 + 剧本摘要/分集梗概 + 缺失视频脚本（含镜头描述）。"""
    result: Dict[str, Any] = {
        "assets": None,
        "outline": None,
        "episode_outlines": 0,
        "video_scripts": {"generated": 0, "failed": 0, "items": [], "errors": []},
    }
    ep_id_set = {str(x).strip() for x in (episode_ids or []) if str(x).strip()}
    asset_id_list = [str(x).strip() for x in (asset_ids or []) if str(x).strip()] or None

    if include_assets:
        result["assets"] = enrich_existing_asset_descriptions(
            project_id, use_llm=use_llm, model=model, asset_ids=asset_id_list
        )

    if include_outline and not asset_id_list and not ep_id_set:
        # 摘要过短则重跑剧本解析（启发式必写；可选 LLM）；有多选时跳过全剧大纲
        proj = services.get_project(project_id)
        docs = proj.get("docs") or {}
        meta = docs.get("script_meta") or {}
        synopsis = (meta.get("synopsis") or "").strip()
        outline_md = (docs.get("outline_md") or "").strip()
        if len(synopsis) < 40 or len(outline_md) < 40:
            try:
                result["outline"] = parse_project_script(
                    project_id, use_llm=use_llm, model=model
                )
            except Exception as e:
                result["outline"] = {"error": str(e)[:200]}
        result["episode_outlines"] = fill_episode_outlines(project_id)

    if include_video_scripts:
        eps = services.list_episodes(project_id)
        if ep_id_set:
            eps = [e for e in eps if e.get("id") in ep_id_set]
            script_limit = max(script_limit, len(eps))
        if not include_assets and not asset_id_list:
            enrich_existing_asset_descriptions(
                project_id, use_llm=False, model=model
            )
        done = 0
        for ep in eps:
            if only_missing_scripts and (ep.get("storyboard_md") or "").strip():
                continue
            if done >= script_limit:
                break
            try:
                # 结构化视频脚本（含风格/锁定/镜头画面台词描述）；避免批量 LLM 超时
                r = generate_episode_video_script(
                    project_id, ep["id"], use_llm=False, model=model
                )
                result["video_scripts"]["items"].append(
                    {
                        "episode_id": ep["id"],
                        "ep_num": ep.get("ep_num"),
                        "shot_count": r.get("shot_count"),
                    }
                )
                result["video_scripts"]["generated"] += 1
            except Exception as e:
                result["video_scripts"]["errors"].append(
                    {
                        "episode_id": ep["id"],
                        "ep_num": ep.get("ep_num"),
                        "error": str(e)[:200],
                    }
                )
                result["video_scripts"]["failed"] += 1
            done += 1
        result["video_scripts"]["note"] = (
            "已生成结构化视频脚本（含镜头描述）。单集可点「整集重新推理」用 AI 精修。"
        )

    result["project"] = services.get_project(project_id)
    result["episodes"] = services.list_episodes(project_id)
    return result


def generate_episode_video_script(
    project_id: str,
    episode_id: str,
    *,
    use_llm: bool = True,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    ep = services.get_episode(episode_id)
    if ep["project_id"] != project_id:
        raise KeyError("episode not in project")
    proj = services.get_project(project_id)
    docs = proj.get("docs") or {}
    meta = {}
    raw_meta = docs.get("script_meta_json") or "{}"
    if isinstance(raw_meta, str):
        try:
            meta = json.loads(raw_meta or "{}")
        except json.JSONDecodeError:
            meta = {}
    elif isinstance(raw_meta, dict):
        meta = dict(raw_meta)
    chars = services.list_characters(project_id)
    scenes = services.list_scenes(project_id)
    props = services.list_props(project_id)
    if use_llm:
        text = llm_video_script(
            ep=ep,
            summary=meta,
            characters=chars,
            scenes=scenes,
            props=props,
            model=model,
        )
    else:
        text = build_video_script_text(
            ep=ep,
            summary=meta,
            characters=chars,
            scenes=scenes,
            props=props,
            segment_sec=15,
        )
    services.update_episode(episode_id, storyboard_md=text)
    # 同步简易镜头：按 Pxxx 段（失败不阻断脚本文本已写入）
    shots = _video_script_to_shots(text)
    if shots:
        try:
            services.replace_shots(episode_id, shots)
        except Exception:
            shots = []
    meta["workflow_step"] = "videos"
    services.update_project_docs(project_id, script_meta_json=meta)
    return {
        "episode_id": episode_id,
        "video_script": text,
        "shot_count": len(shots),
        "episode": services.get_episode(episode_id),
    }


def _field_blocks(part: str, label: str) -> List[str]:
    """提取【标签】后内容：支持「标签后换行」或「标签同行」。"""
    pat = rf"【{re.escape(label)}】\s*(?:\n)?(.*?)(?=\n【|\n镜头\s*\d+|P\d+｜|\Z)"
    return [m.strip() for m in re.findall(pat, part or "", flags=re.S) if m and m.strip()]


def _parse_dialogue_line(line: str) -> Optional[Dict[str, Any]]:
    line = (line or "").strip()
    if not line or line in ("无。", "无", "无对白", "（无）"):
        return None
    # 去掉行首「画外音/OS」前缀
    line = re.sub(r"^(?:画外音|OS|旁白)\s*[:：]\s*", "", line).strip()
    line = re.sub(r"^(?:画外音|OS|旁白)\s+", "", line).strip()
    m = re.match(
        r"^([\u4e00-\u9fffA-Za-z0-9·]{1,16})"
        r"(?:（([^）]*)）|\(([^)]*)\))?"
        r"[：:]\s*"
        r"[「\"“]?(.*?)[」\"”]?\s*$",
        line,
    )
    if not m:
        return None
    text = (m.group(4) or "").strip().strip("「」\"“”")
    if not text or text in ("无", "无。"):
        return None
    return {
        "role": m.group(1),
        "text": text,
        "emotion": (m.group(2) or m.group(3) or "").strip(),
        "start_sec": 0.0,
    }


def _lens_start_sec(block: str) -> Optional[float]:
    """从「镜头N（3—12秒）」或「镜头N（03:00—12:00）」取起始秒。"""
    m = re.search(
        r"镜头\s*\d+\s*[（(]\s*(\d+(?:\.\d+)?)\s*[—\-~～到至]\s*(\d+(?:\.\d+)?)\s*秒?",
        block or "",
    )
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m2 = re.search(
        r"镜头\s*\d+\s*[（(]\s*(\d{1,2}):(\d{2})\s*[—\-~～]",
        block or "",
    )
    if m2:
        try:
            return int(m2.group(1)) * 60 + int(m2.group(2))
        except ValueError:
            return None
    return None


def _video_script_to_shots(text: str) -> List[Dict[str, Any]]:
    """按 P 分段生成镜头：一段 = 一条待生成视频（时长取段总时长，≤15s）。

    段内多个「镜头N」是连续表演提示，会合并进同一条视频；台词/画外音全部提取。
    """
    shots: List[Dict[str, Any]] = []
    parts = re.split(r"(?=^P\d+｜)", text or "", flags=re.M)
    for part in parts:
        part = part.strip()
        if not re.match(r"^P\d+｜", part):
            continue
        hm = re.match(r"^(P\d+｜.+?)(?:\n|$)", part)
        title_line = hm.group(1).strip() if hm else part.splitlines()[0][:80]
        dm = re.search(r"总时长[：:]\s*(\d+)\s*秒", title_line)
        dur = float(dm.group(1)) if dm else 15.0
        dur = max(12.0, min(15.0, dur))

        visuals = _field_blocks(part, "画面")
        visual = "；".join(v for v in visuals if v and v != "无。")[:800] or title_line
        # 清洗旧稿「面对镜头」 residual，避免出图/出视频正对镜头念白
        visual = re.sub(r"面对镜头(?:自然)?说话", "朝向对话对象说话", visual)
        visual = visual.replace("正对镜头念白", "朝向对话对象说话")
        motions = _field_blocks(part, "运镜")
        motion = (motions[0] if motions else "slow cinematic move, keep character identity")[:500]
        pos_blocks = _field_blocks(part, "位置承接")
        continuity = "；".join(p for p in pos_blocks if p and p != "无。")[:500]
        scene_masters = _field_blocks(part, "场景母版")
        if not scene_masters:
            m = re.search(
                r"\[场景母版\]\s*\n(.+?)(?=\n\[|\n镜头\s*\d+|\Z)", part, re.S
            )
            if m:
                scene_masters = [m.group(1).strip()]
        stand_blocks = _field_blocks(part, "站位轴")
        if not stand_blocks:
            m = re.search(
                r"\[站位轴\]\s*\n(.+?)(?=\n\[|\n镜头\s*\d+|\Z)", part, re.S
            )
            if m:
                stand_blocks = [m.group(1).strip()]
        lock_bits = [x for x in (scene_masters[:1] + stand_blocks[:1]) if x]
        if lock_bits:
            visual = ("；".join(lock_bits) + "；" + visual)[:900]
        if continuity:
            continuity = continuity
        elif lock_bits:
            continuity = (
                "同场分段硬衔接：保持场景母版与站位轴，不换景、不翻转左右、不重摆人物。"
            )

        # 按「镜头N」切开，便于给台词对齐开嗓时间
        lens_parts = re.split(r"(?=^镜头\s*\d+)", part, flags=re.M)
        voices: List[Dict[str, Any]] = []
        for lp in lens_parts:
            if not re.match(r"^镜头\s*\d+", lp.strip()):
                # 段头区域也可能有台词
                st = 0.8
            else:
                st = _lens_start_sec(lp)
                if st is None:
                    st = 0.8
            for label in ("台词", "画外音", "OS"):
                for block in _field_blocks(lp, label):
                    for line in block.splitlines() or [block]:
                        parsed = _parse_dialogue_line(line)
                        if not parsed:
                            # 整块可能是「角色：台词」单行
                            parsed = _parse_dialogue_line(block)
                        if not parsed:
                            continue
                        # 画外音默认旁白角色名保留；无角色时用标签
                        if label in ("画外音", "OS") and not parsed.get("role"):
                            parsed["role"] = "旁白"
                        parsed["start_sec"] = float(st)
                        if label in ("画外音", "OS"):
                            parsed["emotion"] = (parsed.get("emotion") or "画外音").strip()
                        voices.append(parsed)
                        break  # 每块取首条有效台词

        # 去重（同角色同文案）
        uniq: List[Dict[str, Any]] = []
        seen = set()
        for v in voices:
            key = (v.get("role"), v.get("text"))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(v)
        voices = uniq[:8]

        # 字数超预算时按比例截短，但尽量保留每位说话人
        budget = _natural_speech_budget(dur)
        total_chars = sum(_dialogue_char_count(v.get("text") or "") for v in voices) or 1
        if total_chars > budget and voices:
            scale = budget / total_chars
            for v in voices:
                t = (v.get("text") or "").strip()
                keep_n = max(6, int(_dialogue_char_count(t) * scale))
                if _dialogue_char_count(t) > keep_n:
                    acc = []
                    n = 0
                    for ch in t:
                        if re.match(r"[\u4e00-\u9fffA-Za-z0-9]", ch):
                            n += 1
                        acc.append(ch)
                        if n >= keep_n:
                            break
                    v["text"] = "".join(acc).rstrip("，、； ") + "…"

        # 若脚本没写镜头时间，再按字数错开
        if voices and all(float(v.get("start_sec") or 0) in (0.0, 0.8) for v in voices):
            t_cursor = 0.8
            usable = max(4.0, float(dur) - 1.5)
            for v in voices:
                v["start_sec"] = round(min(usable - 1.0, max(0.4, t_cursor)), 2)
                chars = _dialogue_char_count(v.get("text") or "")
                t_cursor += max(1.6, chars / 3.2) + 0.45
        else:
            for v in voices:
                try:
                    v["start_sec"] = round(
                        max(0.3, min(float(dur) - 1.2, float(v.get("start_sec") or 0.8))),
                        2,
                    )
                except (TypeError, ValueError):
                    v["start_sec"] = 0.8

        # 有对白时补一条朝向约束（旧脚本缺写时兜底）
        speakers = [
            (v.get("role") or "").strip()
            for v in voices
            if (v.get("role") or "").strip() not in ("旁白", "OS", "os")
            and (v.get("text") or "").strip()
        ]
        uniq_speakers = list(dict.fromkeys(speakers))
        # 站位轴里的对手（避免「晏承嗣朝向晏承嗣」）
        partner = ""
        sm = re.search(
            r"([^\s，、；：:]{2,8})\s*左[、,，]\s*([^\s，、；：:]{2,8})\s*右",
            part + "\n" + visual,
        )
        if sm and uniq_speakers:
            a, b = sm.group(1).strip(), sm.group(2).strip()
            if uniq_speakers[0] == a:
                partner = b
            elif uniq_speakers[0] == b:
                partner = a
            else:
                partner = b if uniq_speakers[0] != b else a
        # 修正已写入的「自己朝向自己」
        visual = re.sub(
            r"([\u4e00-\u9fff]{2,8})朝向\1对谈",
            (rf"\1朝向{partner}对谈" if partner else r"\1朝向对方对谈"),
            visual,
        )
        if uniq_speakers and "朝向" not in visual and "面对" not in visual:
            if len(uniq_speakers) >= 2:
                visual = (
                    f"{uniq_speakers[0]}朝向{uniq_speakers[1]}对谈，眼线看对方；"
                    f"禁止正对镜头念白；{visual}"
                )[:900]
            elif partner:
                visual = (
                    f"{uniq_speakers[0]}朝向{partner}对谈，眼线看对方；"
                    f"禁止正对镜头念白；{visual}"
                )[:900]
            else:
                visual = (
                    f"{uniq_speakers[0]}朝向画内对谈对象/事件方向说话，禁止正对镜头念白；"
                    f"{visual}"
                )[:900]

        row: Dict[str, Any] = {
            "shot_num": len(shots) + 1,
            "duration_sec": dur,
            "title": title_line[:80],
            "visual": visual,
            "motion": motion,
            "voices": voices,
        }
        if continuity:
            row["continuity"] = continuity[:500]
        shots.append(row)
    return shots


