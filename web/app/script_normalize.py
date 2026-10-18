# -*- coding: utf-8 -*-
"""导入剧本 → 冰镜系统标准格式。

标准格式（每集）：
  第001集｜标题

  【梗概】
  ……

  场景1-1 外景·赛车场·夜
  人物：白晓月、池永胜
  △动作描写……
  角色名：对白……
  画外音：……
  角色名（OS）：内心独白……

场标统一为「场景N-M 外景·地点·日/夜」；
出场统一为「人物：」；镜头指示【…】改为「△…」。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# 集标题：第一集 / 第1集 / 第001集｜标题 / 第1集：标题
_EP_START_RE = re.compile(
    r"^[　\s]*第\s*([零〇一二三四五六七八九十百千两\d]+)\s*集"
    r"(?:\s*[｜|:：]\s*(.+?))?\s*$",
    re.MULTILINE,
)
_EP_NUM_RE = re.compile(
    r"^[　\s]*第\s*0*(\d+)\s*集(?:\s*[｜|:：]\s*(.+?))?\s*$",
    re.MULTILINE,
)
_CN_DIGIT = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _cn_to_int(s: str) -> Optional[int]:
    """中文/阿拉伯集号 → int（支持一百内）。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total = 0
    if "百" in s:
        left, _, rest = s.partition("百")
        if not left:
            total += 100
        else:
            d = _CN_DIGIT.get(left)
            if d is None:
                return None
            total += d * 100
        s = rest
        if not s:
            return total
        if s.startswith("零") or s.startswith("〇"):
            s = s[1:]
            if not s:
                return total
    if "十" in s:
        left, _, rest = s.partition("十")
        if not left:
            total += 10
        else:
            d = _CN_DIGIT.get(left)
            if d is None:
                return None
            total += d * 10
        s = rest
        if not s:
            return total if total > 0 else None
        d = _CN_DIGIT.get(s)
        return (total + d) if d is not None else None
    d = _CN_DIGIT.get(s)
    if d is None or d == 0:
        return None
    return total + d if total else d


def _normalize_place(place_blob: str) -> str:
    """赛车场 夜 外 → 外景·赛车场·夜"""
    parts = [p for p in re.split(r"[\s　]+", (place_blob or "").strip()) if p]
    tod = ""
    inout = ""
    place_bits: List[str] = []
    for p in parts:
        if p in ("日", "夜", "晨", "清晨", "黄昏", "傍晚", "午", "午后", "深夜", "凌晨"):
            tod = p if p != "晨" else "清晨"
        elif p in ("内", "外", "内景", "外景"):
            inout = "内" if p.startswith("内") else "外"
        else:
            place_bits.append(p)
    place = "".join(place_bits) or (place_blob or "").strip()
    if not place:
        return ""
    if inout and tod:
        return f"{inout}景·{place}·{tod}"
    if inout:
        return f"{inout}景·{place}"
    if tod:
        return f"{place}·{tod}"
    return place


def _script_body_only(text: str) -> str:
    m = re.search(r"^[　\s]*剧本正文\s*$", text, re.MULTILINE)
    if m:
        return text[m.end() :].lstrip("\n")
    m = re.search(r"剧本正文\s*[\n：:]", text)
    if m:
        return text[m.end() :].lstrip("\n")
    # 从第一个集标题起
    m = re.search(
        r"^[　\s]*第\s*[零〇一二三四五六七八九十百千两\d]+\s*集",
        text,
        re.MULTILINE,
    )
    if m:
        return text[m.start() :]
    return text


def _split_raw_episodes(body: str) -> List[Tuple[int, str, str]]:
    """返回 [(ep_num, raw_title, body), ...]。"""
    matches = list(_EP_START_RE.finditer(body))
    if not matches:
        matches = list(_EP_NUM_RE.finditer(body))
    out: List[Tuple[int, str, str]] = []
    seen = set()
    for i, m in enumerate(matches):
        raw_num = m.group(1)
        title = (m.group(2) or "").strip() if m.lastindex and m.lastindex >= 2 else ""
        n = _cn_to_int(raw_num) if not str(raw_num).isdigit() else int(raw_num)
        if n is None or n <= 0 or n in seen:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = body[start:end].strip()
        seen.add(n)
        out.append((n, title, chunk))
    return out


def _title_from_outline(body: str, fallback: str) -> str:
    m = re.search(r"^[　\s]*梗概\s*[：:]\s*(.+)$", body, re.MULTILINE)
    if m:
        t = re.sub(r"\s+", "", m.group(1).strip())
        if t:
            return t[:40]
    return fallback


def normalize_episode_body(body: str, *, ep_num: int = 1) -> str:
    """把单集正文规范成系统场标/人物/动作/对白格式。"""
    lines_out: List[str] = []
    outline = ""
    for raw in (body or "").splitlines():
        s = raw.strip()
        if not s:
            if lines_out and lines_out[-1] != "":
                lines_out.append("")
            continue
        # 梗概
        m_ol = re.match(r"^梗概\s*[：:]\s*(.+)$", s)
        if m_ol:
            outline = m_ol.group(1).strip()
            continue
        # 短剧场标：1-1 赛车场 夜 外
        m_sc = re.match(
            r"^(\d+)\s*[-–—]\s*(\d+)\s+(.+?)\s*$",
            s,
        )
        if m_sc:
            place = _normalize_place(m_sc.group(3) or "")
            if place:
                if lines_out and lines_out[-1] != "":
                    lines_out.append("")
                lines_out.append(
                    f"场景{int(m_sc.group(1))}-{int(m_sc.group(2))} {place}"
                )
                continue
        # 已有「场景1-1 …」→ 再规范地点串
        m_sc2 = re.match(
            r"^场景\s*(\d+)\s*[-–—]\s*(\d+)\s+(.+?)\s*$",
            s,
        )
        if m_sc2:
            blob = m_sc2.group(3).strip()
            # 已是 外景·x·夜 则保留；否则尝试短剧拆分
            if "·" in blob or blob.startswith(("外景", "内景")):
                place = blob
            else:
                place = _normalize_place(blob) or blob
            if lines_out and lines_out[-1] != "":
                lines_out.append("")
            lines_out.append(
                f"场景{int(m_sc2.group(1))}-{int(m_sc2.group(2))} {place}"
            )
            continue
        # 人： / 人物： / 角色：
        m_pe = re.match(
            r"^【?\s*(?:人|人物|角色|出场人物|主要角色)\s*】?\s*[：:]\s*(.+)$",
            s,
        )
        if m_pe:
            cast = m_pe.group(1).strip()
            lines_out.append(f"人物：{cast}")
            continue
        # 【同镜，分屏】等 → △
        if s.startswith("【") and s.endswith("】") and len(s) <= 40:
            lines_out.append(f"△{s.strip('【】')}")
            continue
        # 手机 VO / VO： → 画外音
        m_vo = re.match(
            r"^(?:手机\s*)?VO\s*[：:]\s*(.+)$",
            s,
            re.I,
        )
        if m_vo:
            lines_out.append(f"画外音：{m_vo.group(1).strip()}")
            continue
        # 角色 OS： / 角色（…）OS：
        m_os = re.match(
            r"^([\u4e00-\u9fffA-Za-z0-9·]{1,16})"
            r"(?:（[^）]{0,20}）|\([^)]{0,20}\))?\s*OS\s*[：:]\s*(.+)$",
            s,
        )
        if m_os:
            lines_out.append(f"{m_os.group(1).strip()}（OS）：{m_os.group(2).strip()}")
            continue
        # 已有 △ / ▲
        if s.startswith("△") or s.startswith("▲"):
            lines_out.append("△" + s.lstrip("△▲").strip())
            continue
        lines_out.append(s)

    # 去掉首尾空行、压缩连续空行
    text = "\n".join(lines_out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    head = ""
    if outline:
        head = f"【梗概】\n{outline}\n\n"
    return head + text


def normalize_imported_script(novel: str) -> str:
    """整本导入文本 → 系统标准剧本（可再 split_novel_episodes）。"""
    text = (novel or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<title>[\s\S]*?</title>\s*", "", text, count=1, flags=re.I)
    text = re.sub(r"(?m)^[ \t]*https?://\S+[ \t]*$", "", text)
    text = text.strip("\n")
    body = _script_body_only(text)
    eps = _split_raw_episodes(body)
    if not eps:
        # 无集标：整篇当第1集规范场标
        norm = normalize_episode_body(body, ep_num=1)
        title = _title_from_outline(body, "第001集")
        return f"第001集｜{title}\n\n{norm}\n"

    blocks: List[str] = []
    for n, title, chunk in eps:
        title = (title or "").strip() or _title_from_outline(chunk, f"第{n:03d}集")
        # 标题里不要再带「第N集」
        title = re.sub(r"^第\s*\d+\s*集\s*[｜|:：]?\s*", "", title).strip() or f"第{n:03d}集"
        if len(title) > 40:
            title = title[:40]
        norm = normalize_episode_body(chunk, ep_num=n)
        blocks.append(f"第{n:03d}集｜{title}\n\n{norm}")
    return "\n\n".join(blocks).strip() + "\n"
