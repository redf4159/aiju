# -*- coding: utf-8 -*-
"""漫剧工作台流水线：导入小说剧本 → 剧本脚本 → 角色/场景/道具 → 视频脚本。"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import db, services
from .drama10_script_llm import chat_completion, script_model

EP_HEAD_RE = re.compile(
    r"^第\s*0*(\d+)\s*集\s*[｜|]\s*(.+?)\s*$", re.MULTILINE
)
# AI漫剧常见：第1集：标题 / 第1集 标题（无竖线）
EP_LOOSE_RE = re.compile(
    r"^[　\s]*第\s*0*(\d+)\s*集\s*[：:\s]+\s*(.+?)\s*$", re.MULTILINE
)
# 【第1集】标题  或  【第1集：标题】（AI漫剧常见，集后全角/半角冒号）
EP_BRACKET_RE = re.compile(
    r"^[　\s]*【\s*第\s*0*(\d+)\s*集\s*"
    r"(?:"
    r"[：:]\s*([^】\n]+?)\s*】"  # 【第1集：标题】
    r"|"
    r"】\s*(.+?)"  # 【第1集】标题
    r")\s*$",
    re.MULTILINE,
)
# EP01《标题》 / EP01｜标题 / EP01 标题
EP_CODE_RE = re.compile(
    r"^[　\s]*EP\s*0*(\d+)\s*(?:[｜|]\s*|(?:《)|[：:\s]+)\s*(.+?)》?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# 短剧台本常见：第一集 / 第二十一集 / 第100集 / 五十四集（偶发漏「第」）
EP_CN_OR_BARE_RE = re.compile(
    r"^[　\s]*(第\s*)?([零〇一二三四五六七八九十百两0-9]{1,12})\s*集\s*"
    r"(?:[｜|：:]\s*(.+?))?\s*$",
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


def chinese_numeral_to_int(raw: str) -> Optional[int]:
    """中文数字转 int：一/十一/二十一/一百/一百零一；也接受纯阿拉伯数字。"""
    s = (raw or "").strip().replace(" ", "").replace("　", "")
    if not s:
        return None
    if re.fullmatch(r"\d{1,4}", s):
        n = int(s)
        return n if n > 0 else None
    if not re.fullmatch(r"[零〇一二三四五六七八九十百两]+", s):
        return None
    # 纯「十」
    if s == "十":
        return 10
    total = 0
    # 百位
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
    # 十位
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
    # 仅个位
    d = _CN_DIGIT.get(s)
    if d is None or d == 0:
        return None
    return total + d if total else d
# 网文「第001章 标题」或「第001章标题」
CHAPTER_HEAD_RE = re.compile(
    r"^[　\s]*第\s*0*(\d+)\s*章\s*([^\n]{0,80}?)\s*$", re.MULTILINE
)
# 飞卢等站点常见：「001 标题」（可有缩进；标题以汉字/书名号等开头）
NUM_CHAPTER_HEAD_RE = re.compile(
    r"^[ \t]{0,40}(\d{1,3})[ \t]+"
    r"([《「『【（(\[“]?[\u4e00-\u9fff][^\n\r]{0,78}?)\s*$",
    re.MULTILINE,
)
SCENE_RE = re.compile(
    r"^(?:[\t 　]*)(EP\d+-SC\d+)\s*[｜|]\s*(.+?)\s*$", re.MULTILINE
)
# AI漫剧场标：场景：xxx / 【场景：xxx】 / 地点：xxx / 场景1-1  外景·码头
SCENE_LABEL_RE = re.compile(
    r"【?\s*(?:场景|地点|布景)\s*[：:]\s*([^】\n，,]{2,40})】?"
)
SCENE_NUM_RE = re.compile(
    r"^[　\s]*场景\s*(\d+)\s*[-–—]\s*(\d+)\s+(.+?)\s*$", re.MULTILINE
)
# 短剧场标：1-1 赛车场 夜 外 / 1-2 路边 日 外（无「场景」前缀）
SHORT_SCENE_RE = re.compile(
    r"^[　\s]*(\d+)\s*[-–—]\s*(\d+)\s+([^\n]{2,40}?)\s*$",
    re.MULTILINE,
)
# 角色表：角色：A、B / 【角色】A、B / 人：A、B
CAST_LINE_RE = re.compile(
    r"^[　\s]*【?\s*(?:角色|出场人物|主要角色)\s*】?\s*[：:]\s*(.+?)\s*$",
    re.MULTILINE,
)
# 网文地点提示：来到会议室 / 走进办公室 …
NOVEL_SCENE_HINT_RE = re.compile(
    r"(?:来到|走进|回到|站在|位于|身处|前往|出现在|抵达|进入|坐在|躺在|停在)"
    r"([\u4e00-\u9fffA-Za-z0-9·]{0,6}"
    r"(?:办公室|会议室|会客室|客厅|卧室|别墅|公司|大楼|街道|巷口|学校|教室|"
    r"酒店|宾馆|餐厅|咖啡馆|咖啡厅|基地|现场|剧组|摄影棚|片场|电影院|"
    r"书房|走廊|大厅|门口|天台|地下室|车库|停车场|病房|医院|警局|"
    r"茶水间|休息室|导演室|放映厅|演播厅|工作室|工位|宿舍|家里|家中|"
    r"阳台|厨房|卫生间|浴室|电梯|地铁|公交|飞机|车上|车内|船上))"
)
# 人物：A、B  或短剧常见「人：A、B」
PEOPLE_RE = re.compile(r"^[ \t　]*人物?[：:]\s*(.+?)\s*$", re.MULTILINE)


def _is_script_preamble_line(s: str) -> bool:
    """集标题 / 梗概 / 出场人物行：不进场次正文，也不当对白。"""
    t = (s or "").strip()
    if not t:
        return True
    if re.match(r"^第\s*\d+\s*集", t) or (t.startswith("第") and "｜" in t[:20]):
        return True
    if re.match(r"^梗概\s*[：:]", t):
        return True
    if PEOPLE_RE.match(t) or CAST_LINE_RE.match(t):
        return True
    if t.startswith("人：") or t.startswith("人:"):
        return True
    return False


def _normalize_short_scene_name(place_blob: str) -> str:
    """把「赛车场 夜 外」规范成「外景·赛车场·夜」。"""
    parts = [p for p in re.split(r"[\s　]+", (place_blob or "").strip()) if p]
    tod = ""
    inout = ""
    place_bits: List[str] = []
    for p in parts:
        if p in ("日", "夜", "晨", "清晨", "黄昏", "傍晚", "午", "午后", "深夜", "凌晨"):
            tod = p
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


def _split_cast_names(raw: str) -> List[str]:
    """解析「白晓月、池永胜」或「（白媛媛）白晓月」。返回原始片段供 identity 解析。"""
    out: List[str] = []
    for part in re.split(r"[、,，/|｜]|与|和", raw or ""):
        part = (part or "").strip()
        if not part:
            continue
        # （白媛媛）白晓月 → 两个名都收
        for m in re.finditer(r"[（(]([^）)]{1,16})[）)]", part):
            out.append(m.group(1).strip())
        core = re.sub(r"[（(][^）)]*[）)]", "", part).strip()
        if core:
            out.append(core)
    # 去重保序
    seen = set()
    uniq = []
    for n in out:
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq
# 必须 MULTILINE：对白分布在各行，不能只匹配全文开头；兼容全角缩进
DIALOGUE_RE = re.compile(
    r"^[ \t　]*([\u4e00-\u9fffA-Za-z0-9·]{1,16})(?:（[^）]*）|\([^)]*\))?[：:]\s*(.+)$",
    re.MULTILINE,
)
# 网文说话人：姓名(2-4字非贪婪) + 语气动词 + 引号。
# 非贪婪避免「陈野冷声道」吞成「陈野冷声」；复合语气词须排在单字「道/说」前。
NOVEL_SPEAKER_RE = re.compile(
    r"(?<![\u4e00-\u9fff])"
    r"([\u4e00-\u9fff·]{2,4}?)"
    r"(?:（[^）]{0,10}）)?"
    r"(?:"
    r"冷声道|怒声道|低声道|轻声道|大声道|高声道|沉声道|柔声道|"
    r"笑道|怒道|喝道|问道|说道|喊道|叫道|答道|回道|"
    r"道|说|问|喊|叫|吼"
    r")"
    r"[：:]\s*[「\"“]"
)
# 对白/说话人误吸入的非角色词
_NON_CHAR_NAMES = frozenset(
    {
        "OS",
        "画外音",
        "旁白",
        "字幕",
        "众人",
        "两者",
        "内心",
        "版本",
        "定位",
        "人物",
        "人",
        "梗概",
        "场景",
        "道具",
        "标题",
        "作者",
        "简介",
        "章名",
        "时间",
        "地点",
        "备注",
        "说明",
        "风格",
        "画风",
        "模式",
        "这时",
        "突然",
        "只见",
        "随后",
        "于是",
        "然后",
        "但是",
        "因为",
        "所以",
        "如果",
        "虽然",
        "冷声",
        "怒声",
        "低声",
        "轻声",
        "大声",
        "高声",
        "沉声",
        "柔声",
        "汇报",
        "通知",
        "宣布",
        "表示",
        "继续",
        "开始",
        "结束",
        "完成",
        "出现",
        "发生",
        "进行",
        "通过",
        "根据",
        "按照",
        "关于",
        "对于",
        "作为",
        "已经",
        "可能",
        "应该",
        "忽然",
        "缓缓",
        "轻轻",
        "暗暗",
        "微微",
        "连忙",
        "赶忙",
        "赶紧",
        "立刻",
        "马上",
        "终于",
        "竟然",
        "居然",
        "其实",
        "原来",
        "因此",
        "然而",
        "不过",
        "可是",
        "为了",
        "自己",
        "对方",
        "大家",
        "有人",
        "某人",
        "对方",
        "系统",
        "提示",
        "本章",
        "正文",
        "前言",
        "后记",
        "一声",
        "两声",
        "三声",
        "开口",
        "接话",
        "插嘴",
        "回应",
        "回答",
        "答复",
        "传话",
        "喊话",
        "讲话",
        "说话",
        "出声",
        "发声",
        "闷声",
        "清嗓",
        "点头",
        "摇头",
        "面色",
        "神色",
        "目光",
        "眼神",
        "语气",
        "声音",
        "工作人员",
        "众人齐",
        "齐声",
        "异口",
        "同声",
        "梗概",
        "大纲",
        "别名",
        "亮点",
        "频道",
        "路人",
        "路人甲",
        "路人乙",
        "路人若干",
        "群众",
        "若干",
    }
)
# 不像人名的尾缀/用词（勿含「然/也/才」等常见名尾，避免误杀白晓然等）
_NON_CHAR_SUFFIX_RE = re.compile(
    r"(?:声|着|地|得|了|过|的|吧|啊|呢|吗|么|样|般|"
    r"笑|哭|怒|急|忙|快|慢|"
    r"说|道|问|喊|看|听|想|见|到|来|去|出|入|回)$"
)
_NON_CHAR_CONTAINS_RE = re.compile(
    r"(?:工作人员|领导|同事|网友|观众|记者|导演|编剧|系统提示|章节|第.章)"
)


_NON_CHAR_PREFIXES = (
    "随后",
    "这时",
    "此时",
    "然后",
    "于是",
    "忽然",
    "突然",
    "接着",
    "同时",
    "立刻",
    "马上",
    "终于",
    "竟然",
    "居然",
    "其实",
    "原来",
    "因此",
    "然而",
    "不过",
    "可是",
    "只见",
    "但见",
    "便见",
)


def _clean_char_name(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    # 去掉旁白/OS 后缀与群像噪声
    n = re.sub(r"^(?:人物|角色)\s*[：:]\s*", "", n).strip()
    n = re.sub(r"(?:\s*(?:OS|os|Os|画外音|旁白|VO|vo))+$", "", n).strip()
    n = re.sub(r"[*＊×xX]\s*\d+$", "", n).strip()
    n = re.sub(r"若干$", "", n).strip()
    # 「张三道：“…」被对白正则吞成「张三道」时，去掉尾动词
    if len(n) >= 3 and n[-1] in "道说问喊叫吼喝答回" and n not in _NON_CHAR_NAMES:
        n = n[:-1]
    if n in _NON_CHAR_NAMES:
        return ""
    if len(n) < 2 or len(n) > 8:
        return ""
    if any(ch.isdigit() for ch in n):
        return ""
    if any(n.startswith(p) or n == p for p in _NON_CHAR_PREFIXES):
        return ""
    # 「随后汇报 / XX通知」等叙事+动词
    if n.endswith(("汇报", "通知", "宣布", "表示", "回应", "回答", "传话", "喊话")):
        return ""
    if _NON_CHAR_CONTAINS_RE.search(n):
        return ""
    # 排除「冷声/赶紧/一笑」等不像姓名的片段
    if _NON_CHAR_SUFFIX_RE.search(n) and n not in {"少爷", "小姐", "先生", "大人", "夫人"}:
        # 双字纯语气词直接丢；更长的若以语气尾缀结尾也丢
        if len(n) <= 3 or n[-1] in "声着地得了吧啊呢吗":
            return ""
    return n


# 同人异名/穿书前身份 → (主角色名, 状态标签)；多年龄态并入同一角色多图
_CHAR_ALIAS_TO_CANON: Dict[str, Tuple[str, str]] = {
    "白媛媛": ("白晓月", "穿书前·外卖少女"),
}

_AGE_CHAR_RE = re.compile(
    r"^([0-9]{1,2}|[零一二三四五六七八九十两]{1,4})岁的?(.+)$"
)
_ERA_CHAR_RE = re.compile(r"^(幼年|童年|少年|少女|青年|成年|老年)的?(.+)$")


def _resolve_character_identity(raw: str) -> Tuple[str, str]:
    """把「十一岁的白晓月」「白媛媛」归并到主角色，返回 (主名, 状态标签)。"""
    s = (raw or "").strip()
    s = re.sub(r"^(?:人物|角色)\s*[：:]\s*", "", s).strip()
    if not s:
        return "", ""
    if s in _CHAR_ALIAS_TO_CANON:
        return _CHAR_ALIAS_TO_CANON[s]
    m = _AGE_CHAR_RE.match(s)
    if m:
        age_raw, rest = m.group(1), m.group(2)
        # 允许「十一岁的白晓月」整体超长，只清洗后缀人名
        base = _clean_char_name(rest)
        if not base:
            return "", ""
        if age_raw.isdigit():
            age_n = int(age_raw)
        else:
            age_n = chinese_numeral_to_int(age_raw) or 0
        # 低龄统一用「幼年/青涩」标签，避免出图 API 因「N岁」拦截
        if age_n and age_n <= 14:
            label = "回忆杀·青涩态"
        else:
            label = f"{age_n}岁形态" if age_n else "回忆杀·青涩态"
        return base, label
    m2 = _ERA_CHAR_RE.match(s)
    if m2:
        base = _clean_char_name(m2.group(2))
        if base:
            era = m2.group(1)
            label = "回忆杀·青涩态" if era in ("幼年", "童年", "少年", "少女") else f"{era}形态"
            return base, label
    return _clean_char_name(s), ""


def _extract_face_lock(canon: str, base_visual: str = "") -> str:
    """从主角锚点或基础 visual_prompt 抽出五官锁，供多身份态共用同一张脸。"""
    anchor = _CAST_LOOK_ANCHORS.get((canon or "").strip()) or {}
    face = (anchor.get("face") or "").strip()
    if face:
        return face
    vp = (base_visual or "").strip()
    m = re.search(r"五官[：:]\s*([^；;\n]+)", vp)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:脸型|面部)[：:]\s*([^；;\n]+)", vp)
    if m:
        return m.group(1).strip()
    return "与基础设定图同一张脸、同一五官骨架、同一瞳色与肤色"


def _child_state_appearance(
    canon: str,
    state_label: str,
    art_style: str = "",
    base_visual: str = "",
) -> str:
    """为年龄/身份态生成外表描述：脸必须与基础态同一人，只改年龄感与服装发型。

    注意：出图 API 对低龄描述敏感，回忆杀统一用「青涩少女/少年早期」表述，避免幼儿词。
    """
    st = state_label or ""
    age_m = re.search(r"(\d{1,2})岁", st)
    age_n = int(age_m.group(1)) if age_m else 0
    face = _extract_face_lock(canon, base_visual)
    face_lock = (
        f"【脸部身份锁定·同一个人】五官必须与{canon}基础态完全一致：{face}；"
        f"禁止换脸、换眼型、换鼻型、换嘴型、换脸型骨架或瞳色肤色；"
        f"仅允许年龄感、妆感、发型与服装变化。"
    )
    if "外卖" in st or "穿书前" in st:
        return (
            f"{canon}（{st}）：22岁年轻女性；{face_lock}"
            f"发型改为黑色长直发或低马尾（生活感，可略显疲惫）；"
            f"外卖骑手黄蓝外套+牛仔裤运动鞋；气质接地气，但脸仍是同一个人，"
            f"与贵气成年态仅服装发型气质不同。"
        )
    if (age_n and age_n <= 14) or any(
        k in st for k in ("幼年", "童年", "少年", "少女", "青涩")
    ):
        return (
            f"{canon}（回忆杀·青涩态/{st}）：同一人物更年轻的青涩造型；{face_lock}"
            f"年龄感更青涩、妆面更淡或素颜、脸部软组织略圆润，但骨相五官仍是同一人；"
            f"发型可改为齐肩直发/双马尾/空气刘海等学生风；"
            f"浅色连衣裙或学生风穿搭；与成年基础态仅年龄感与服装发型不同。"
        )
    return (
        f"{canon}（{st}）：同一人物的不同时期造型；{face_lock}"
        f"年龄感、发型、服装与基础态可区分，但必须一眼可认出是同一个人。"
    )


def _filter_weak_character_hits(
    chars: Dict[str, Dict[str, Any]], *, min_hits: int = 2
) -> Dict[str, Dict[str, Any]]:
    """网文启发式：丢低频说话人噪声；人物表/多集出场的保留。"""
    kept: Dict[str, Dict[str, Any]] = {}
    for name, slot in chars.items():
        hits = int(slot.get("_hits") or 0)
        eps = slot.get("episodes") or []
        lines = slot.get("_lines") or []
        # 有对白文本、或多集出现、或命中次数够 → 保留
        if lines or len(eps) >= 2 or hits >= min_hits:
            kept[name] = slot
            continue
        # 单次「xx道」噪声丢掉
        if hits <= 1 and not lines:
            continue
        kept[name] = slot
    return kept


def _word_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def clean_novel_text(novel: str) -> str:
    """去掉导入噪声：HTML title、飞卢推广链等。保留行首缩进（章标依赖缩进/空行）。"""
    text = (novel or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<title>[\s\S]*?</title>\s*", "", text, count=1, flags=re.I)
    text = re.sub(
        r"(?m)^[ \t]*https?://\S+[ \t]*$",
        "",
        text,
    )
    text = re.sub(
        r"请安装飞卢小说[^\n]{0,120}",
        "",
        text,
    )
    # 只去掉首尾空行，不要 strip 掉首章标题前的缩进空格
    return text.strip("\n")


def _match_title(m: re.Match) -> str:
    """兼容多捕获组标题（如【第N集：标题】的 group2 / 【第N集】标题的 group3）。"""
    for i in range(2, (m.lastindex or 1) + 1):
        t = (m.group(i) or "").strip().strip("：:|-_　 。.")
        if t:
            return t
    return ""


def _title_from_episode_body(body: str, ep_num: int, label: str = "集") -> str:
    """无标题时，用梗概首句或默认「第00N集」。"""
    m = re.search(
        r"^[　\s]*梗概\s*[：:]\s*(.+)$",
        body or "",
        re.MULTILINE,
    )
    if m:
        t = re.sub(r"\s+", "", m.group(1).strip())
        if t:
            return t[:40]
    return f"第{ep_num:03d}{label}"


def _split_by_matches(
    text: str,
    matches: List[re.Match],
    *,
    label: str = "集",
    require_blank_before: bool = False,
    num_from_match: Optional[Callable[[re.Match], Optional[int]]] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if num_from_match is not None:
            ep_num = num_from_match(m)
        else:
            try:
                ep_num = int(m.group(1))
            except (TypeError, ValueError):
                continue
        if ep_num is None or ep_num <= 0 or ep_num > 500 or ep_num in seen:
            continue
        # 自定义集号解析时（中文「第一集」），标题在最后一组，勿把集号当标题
        if num_from_match is not None:
            title = ""
            if m.lastindex and m.lastindex >= 3:
                title = (m.group(m.lastindex) or "").strip().strip("：:|-_　 。.")
        else:
            title = _match_title(m)
        if not title:
            title = _title_from_episode_body(body, int(ep_num), label=label)
        # 过滤明显非标题（URL / HTML）
        if re.search(r"https?://|</?\w+>", title):
            continue
        # 数字章标：前一行应为空（或文本开头），避免正文「100 个人」误切
        if require_blank_before:
            pre = text[: m.start()]
            if pre:
                prev_line = pre.rsplit("\n", 1)[-1]
                if prev_line.strip():
                    continue
        seen.add(ep_num)
        # 去掉正文开头重复的「001标题」粘连行
        body = re.sub(
            rf"^[　\s]*0*{int(ep_num)}[　\s]*{re.escape(title[:12])}[^\n]*\n*",
            "",
            body,
            count=1,
        ).strip()
        out.append(
            {
                "ep_num": int(ep_num),
                "title": title[:120],
                "script_md": f"第{int(ep_num):03d}集｜{title}\n\n{body}".strip(),
                "word_count": _word_count(body),
            }
        )
    out.sort(key=lambda e: int(e["ep_num"]))
    return out


def _script_body_for_split(text: str) -> str:
    """若含「剧本正文」标记，优先只切正文，避免大纲里的「1-10 集」干扰。"""
    m = re.search(r"^[　\s]*剧本正文\s*$", text, re.MULTILINE)
    if m:
        return text[m.end() :].lstrip("\n")
    m = re.search(r"剧本正文\s*[\n：:]", text)
    if m:
        return text[m.end() :].lstrip("\n")
    return text


def split_novel_episodes(novel: str) -> List[Dict[str, Any]]:
    """按多种集/章标题切分：第N集｜、第一集、EP01《》、【第N集】、第N章、001 标题。"""
    text = clean_novel_text(novel)
    if not text:
        return []
    body = _script_body_for_split(text)

    def _try_patterns(src: str) -> List[Dict[str, Any]]:
        for pat in (EP_HEAD_RE, EP_BRACKET_RE, EP_LOOSE_RE, EP_CODE_RE):
            matches = list(pat.finditer(src))
            if len(matches) >= 1 and (
                len(matches) >= 2 or pat in (EP_HEAD_RE, EP_BRACKET_RE, EP_CODE_RE)
            ):
                # 单集剧本也允许 EP01 / 第001集｜ 切出一集
                split = _split_by_matches(src, matches, label="集")
                if split:
                    return split
        # 中文集号 / 仅「第N集」无标题（如《恶毒女配逆袭了》）
        cn_matches = list(EP_CN_OR_BARE_RE.finditer(src))
        if len(cn_matches) >= 2:
            def _cn_num(m: re.Match) -> Optional[int]:
                # group1=可选「第」，group2=数字，group3=标题
                has_di = bool((m.group(1) or "").strip())
                n = chinese_numeral_to_int(m.group(2) or "")
                if n is None:
                    return None
                # 漏写「第」时：前一行须为空，避免正文「一集」误切
                if not has_di:
                    pre = src[: m.start()]
                    if pre:
                        prev = pre.rsplit("\n", 1)[-1]
                        if prev.strip():
                            return None
                return n

            split = _split_by_matches(
                src,
                cn_matches,
                label="集",
                num_from_match=_cn_num,
            )
            if len(split) >= 2:
                return split
        matches = list(CHAPTER_HEAD_RE.finditer(src))
        # 过滤文件名残留「第1章-50章」
        matches = [
            m
            for m in matches
            if not re.match(r"^-\d+", (m.group(2) or "").strip())
        ]
        if len(matches) >= 2:
            return _split_by_matches(src, matches, label="章")
        matches = list(NUM_CHAPTER_HEAD_RE.finditer(src))
        split = _split_by_matches(
            src, matches, label="章", require_blank_before=True
        )
        if len(split) >= 2:
            return split
        return []

    # 先切正文；正文不够再回退全文
    split = _try_patterns(body)
    if split:
        return split
    if body is not text:
        split = _try_patterns(text)
        if split:
            return split
    # 单章/整本兜底
    return [
        {
            "ep_num": 1,
            "title": "第001集",
            "script_md": text,
            "word_count": _word_count(text),
        }
    ]


def _aspect_to_wh(aspect_ratio: str) -> Tuple[int, int]:
    ar = (aspect_ratio or "").strip()
    if ar == "9:16":
        return 720, 1280
    return 1280, 720


def sync_project_episodes(
    project_id: str, episodes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """按切分结果写入/扩容分集（缺集则插入），并更新 projects.episode_count。

    只更新 title / script_md / outline，绝不清空或改写 storyboard_md（视频脚本）。
    """
    if not episodes:
        return []
    existing = {int(e["ep_num"]): e for e in services.list_episodes(project_id)}
    max_num = max(int(e["ep_num"]) for e in episodes)
    with db.connect() as conn:
        for e in episodes:
            n = int(e["ep_num"])
            title = (e.get("title") or f"第{n:03d}集")[:120]
            script = e.get("script_md") or ""
            outline = f"{e.get('word_count') or 0}字"
            if n in existing:
                # 注意：UPDATE 列不含 storyboard_md，保留已生成视频脚本
                conn.execute(
                    """
                    UPDATE episodes SET title=?, script_md=?, outline=?
                    WHERE project_id=? AND ep_num=?
                    """,
                    (title, script, outline, project_id, n),
                )
            else:
                eid = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO episodes (id, project_id, ep_num, title, script_md, outline, pipeline_json)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        eid,
                        project_id,
                        n,
                        title,
                        script,
                        outline,
                        json.dumps(services._default_episode_pipeline(), ensure_ascii=False),
                    ),
                )
        conn.execute(
            "UPDATE projects SET episode_count=?, updated_at=? WHERE id=?",
            (max_num, db._utcnow(), project_id),
        )
    return episodes


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
                '"era":"时代背景"}'
                "不要输出或改写画风；画风由用户指定，与你无关。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"本剧已定画风（请勿修改）：{art_style}\n"
                f"分集列表：\n{ep_lines}\n\n剧本正文：\n{sample}"
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
            # 画风以用户/项目设置为准，禁止 LLM 覆盖
            "art_style": art_style or base["art_style"],
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

    def _char(name: str, state: str = "") -> Dict[str, Any]:
        slot = chars.setdefault(
            name,
            {
                "name": name,
                "role_note": "",
                "visual_prompt": "",
                "voice_desc": "",
                "episodes": [],
                "_lines": [],
                "_actions": [],
                "_hits": 0,
                "_from_people": False,
                "_states": [],
            },
        )
        if state:
            sts = slot.setdefault("_states", [])
            if state not in sts:
                sts.append(state)
        return slot

    for ep in episodes:
        ep_num = int(ep.get("ep_num") or 0)
        body = ep.get("script_md") or ""
        lines = body.splitlines()
        for cre in (PEOPLE_RE, CAST_LINE_RE):
            for m in cre.finditer(body):
                for raw in _split_cast_names(m.group(1)):
                    name, state = _resolve_character_identity(raw)
                    if not name:
                        continue
                    slot = _char(name, state)
                    slot["_from_people"] = True
                    slot["_hits"] = int(slot.get("_hits") or 0) + 3
                    if ep_num and ep_num not in slot["episodes"]:
                        slot["episodes"].append(ep_num)
        for m in DIALOGUE_RE.finditer(body):
            name, state = _resolve_character_identity(m.group(1) or "")
            if not name:
                continue
            slot = _char(name, state)
            slot["_hits"] = int(slot.get("_hits") or 0) + 1
            if ep_num and ep_num not in slot["episodes"]:
                slot["episodes"].append(ep_num)
            text = (m.group(2) or "").strip()
            if text and len(slot["_lines"]) < 6:
                slot["_lines"].append(text[:80])
        # 网文说话人：张三道：“…”
        for m in NOVEL_SPEAKER_RE.finditer(body):
            name, state = _resolve_character_identity(m.group(1) or "")
            if not name or len(name) > 4:
                continue
            slot = _char(name, state)
            slot["_hits"] = int(slot.get("_hits") or 0) + 1
            if ep_num and ep_num not in slot["episodes"]:
                slot["episodes"].append(ep_num)
        for ln in lines:
            s = ln.strip()
            if s.startswith("▲") or s.startswith("△"):
                act = s.lstrip("▲△").strip()
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
            short = SHORT_SCENE_RE.match(s)
            if short:
                code = f"SC{int(short.group(1)):02d}-{int(short.group(2)):02d}"
                name = _normalize_short_scene_name(short.group(3) or "")
                if not name:
                    continue
                current_scene_key = name
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
                if not slot.get("code"):
                    slot["code"] = code
                if ep_num and ep_num not in slot["episodes"]:
                    slot["episodes"].append(ep_num)
                continue
            if current_scene_key and (s.startswith("▲") or s.startswith("△")):
                act = s.lstrip("▲△").strip()
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
        # 场景：xxx / 【场景：xxx】 / 场景1-1  外景·码头
        for sm in SCENE_LABEL_RE.finditer(body):
            name = (sm.group(1) or "").strip().strip("【】[]")
            if not name or len(name) < 2 or len(name) > 30:
                continue
            slot = scenes.setdefault(
                name,
                {
                    "code": "",
                    "name": name,
                    "visual_prompt": "",
                    "role_note": "",
                    "episodes": [],
                    "_actions": [],
                },
            )
            if ep_num and ep_num not in slot["episodes"]:
                slot["episodes"].append(ep_num)
        for sm in SCENE_NUM_RE.finditer(body):
            code = f"SC{int(sm.group(1)):02d}-{int(sm.group(2)):02d}"
            name = (sm.group(3) or "").strip()
            if not name:
                continue
            key = name
            slot = scenes.setdefault(
                key,
                {
                    "code": code,
                    "name": name,
                    "visual_prompt": "",
                    "role_note": "",
                    "episodes": [],
                    "_actions": [],
                },
            )
            if not slot.get("code"):
                slot["code"] = code
            if ep_num and ep_num not in slot["episodes"]:
                slot["episodes"].append(ep_num)
        # 网文地点：来到会议室 / 走进办公室 …
        # 若本集已有规范场标（SC01-01 / 外景·…），不再用碎片提示污染场景库
        has_structured_scene = any(
            (s.get("code") or "").startswith("SC") or "·" in (s.get("name") or "")
            for s in scenes.values()
            if ep_num in (s.get("episodes") or [])
        )
        if not has_structured_scene:
            for sm in NOVEL_SCENE_HINT_RE.finditer(body):
                name = (sm.group(1) or "").strip()
                if not name or len(name) < 2 or len(name) > 16:
                    continue
                if name in _NON_CHAR_NAMES:
                    continue
                slot = scenes.setdefault(
                    name,
                    {
                        "code": "",
                        "name": name,
                        "visual_prompt": "",
                        "role_note": "",
                        "episodes": [],
                        "_actions": [],
                    },
                )
                if ep_num and ep_num not in slot["episodes"]:
                    slot["episodes"].append(ep_num)
                ctx = sm.group(0)
                if ctx and len(slot["_actions"]) < 6:
                    slot["_actions"].append(ctx[:120])

    # 有「人物：」表则信任表内角色；纯网文则按说话频次过滤噪声
    has_people_line = any(c.get("_from_people") for c in chars.values())
    if has_people_line:
        filtered_chars = {
            k: v for k, v in chars.items() if v.get("_from_people") or int(v.get("_hits") or 0) >= 2
        }
    else:
        filtered_chars = _filter_weak_character_hits(chars, min_hits=2)
    out = {
        "characters": list(filtered_chars.values()),
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
    # 旧模板：所有角色都是「古装+沉稳干练」，视为弱描述必须重写
    if "古装人物" in t and ("站姿沉稳干练" in t or "身份气质稳定，供多集复用" in t):
        return True
    if "五官清晰、服饰材质可读" in t and "供多集复用" in t and len(t) < 220:
        return True
    return False


def _detect_story_era(*blobs: str) -> str:
    """modern=都市现造；ancient=古装/架空。"""
    text = "\n".join(b for b in blobs if b)
    modern_keys = (
        "手机", "外卖", "赛车", "公司", "董事", "别墅", "电动车", "酒店", "保安",
        "奶茶", "轿车", "西装", "高跟鞋", "总裁", "办公室", "电梯", "酒吧", "夜店",
        "跑车", "合同", "股票", "直播", "短视频",
    )
    ancient_keys = (
        "侯府", "府邸", "丫鬟", "公子", "殿下", "朝廷", "长衫", "发髻", "锦袍",
        "书院", "江湖", "镖局", "将军府", "丞相", "皇上", "侧妃", "道袍",
    )
    m = sum(1 for k in modern_keys if k in text)
    a = sum(1 for k in ancient_keys if k in text)
    if m >= 2 and m >= a:
        return "modern"
    if a >= 2 and a > m:
        return "ancient"
    return "modern" if m > a else "ancient"


# 已知主角固定外貌锚点（避免同剧撞脸）；未列名再按性别年龄哈希分化
_CAST_LOOK_ANCHORS: Dict[str, Dict[str, str]] = {
    "白晓月": {
        "gender": "女",
        "age": "23岁年轻女性",
        "face": "精致鹅蛋脸、桃花眼略上挑、高鼻梁、薄唇带锋利感、肤色白皙冷调",
        "hair": "黑色大波浪长发及腰，发丝有光泽，侧分额前碎发",
        "body": "高挑匀称约168，肩线利落，气场强",
        "outfit_modern": "高定暗红或黑色修身连衣裙+细高跟鞋，耳饰简约金属，贵气张扬",
        "outfit_ancient": "绯红织金长裙外罩黑色薄纱，发饰华丽但克制",
        "vibe": "穿书后清醒飒爽的恶毒女配逆袭感，眼神锐利带笑意",
    },
    "白媛媛": {
        # 与白晓月同人（穿书前身份）：脸必须一致，仅服装发型气质不同
        "gender": "女",
        "age": "22岁年轻女性",
        "face": "精致鹅蛋脸、桃花眼略上挑、高鼻梁、薄唇带锋利感、肤色白皙冷调",
        "hair": "黑色长直发或低马尾，略显疲惫生活感",
        "body": "偏纤细约162，日常体态",
        "outfit_modern": "外卖骑手黄蓝外套+头盔可摘下、牛仔裤运动鞋，或休闲卫衣",
        "outfit_ancient": "素色布衣裙，朴素发带",
        "vibe": "穿越前普通外卖女孩，接地气，但脸仍是白晓月同一人",
    },
    "池永胜": {
        "gender": "男",
        "age": "26岁年轻男性",
        "face": "深邃剑眉、狭长眼、高鼻、薄唇，痞帅带危险感",
        "hair": "黑色短发微乱、发丝略翘，侧分",
        "body": "精悍修长约183，肩宽腰窄",
        "outfit_modern": "黑色皮衣或深色定制西装敞开领，手表与戒指，赛车手气质",
        "outfit_ancient": "玄色劲装外罩暗纹长袍，腰系窄带",
        "vibe": "恶毒男配亦穿书，玩世不恭却藏锋",
    },
    "白晓然": {
        "gender": "女",
        "age": "21岁年轻女性",
        "face": "软萌圆脸、水汪汪杏眼、小巧鼻、粉唇，表面楚楚可怜",
        "hair": "浅棕或自然黑长直发，齐刘海或空气刘海",
        "body": "娇小约158，肩软体态柔",
        "outfit_modern": "浅粉/米白娃娃领连衣裙或针织开衫，珍珠耳钉，白莲花感",
        "outfit_ancient": "淡粉罗裙、素银发簪，柔弱感",
        "vibe": "原书女主白莲外表，眼神深处可藏嫉妒",
    },
    "陆城": {
        "gender": "男",
        "age": "30岁成年男性",
        "face": "浓眉、深眼窝、方正下颌、薄唇紧抿，压迫感强",
        "hair": "黑色背头短发，整齐油光克制",
        "body": "宽肩挺拔约185，总裁体态",
        "outfit_modern": "黑色三件套西装+领带，袖扣精致，暴戾霸道总裁",
        "outfit_ancient": "玄色官袍或劲装披氅，腰佩玉",
        "vibe": "原书男主，偏执强势",
    },
    "白建雄": {
        "gender": "男",
        "age": "52岁中年男性",
        "face": "国字脸、眼尾细纹、鼻梁挺、法令纹可见，威严慈中带偏心",
        "hair": "黑发夹灰鬓角，短发整齐",
        "body": "微微发福约175，商贾家主体态",
        "outfit_modern": "深灰商务西装或羊绒大衣，腕表厚重",
        "outfit_ancient": "藏青锦袍外罩马褂，玉扳指",
        "vibe": "白家父亲，偏爱长女",
    },
    "何红丽": {
        "gender": "女",
        "age": "48岁中年女性",
        "face": "成熟鹅蛋脸、细眉、眼尾轻纹、妆容精致，贵妇气场",
        "hair": "黑色盘发或大波浪及肩，夹少量灰丝",
        "body": "丰腴端庄约165",
        "outfit_modern": "香奈儿风套装或丝质旗袍裙，珍珠项链与耳环",
        "outfit_ancient": "绛紫华服、金步摇，雍容",
        "vibe": "白家母亲，宠女护短",
    },
    "慧慧": {
        "gender": "女",
        "age": "24岁年轻女性",
        "face": "尖圆脸、笑眼、小酒窝、亲和感",
        "hair": "棕色微卷齐肩发，发卡点缀",
        "body": "匀称约160",
        "outfit_modern": "干练衬衫+半身裙或休闲西装配色明亮",
        "outfit_ancient": "浅绿侍女装或闺中常服",
        "vibe": "闺蜜/助理型配角，活泼",
    },
    "池昌": {
        "gender": "男",
        "age": "55岁中年男性",
        "face": "宽脸、浓眉、眼袋略重、嘴唇厚，老谋深算",
        "hair": "花白短发，背头",
        "body": "敦实约172",
        "outfit_modern": "深色唐装或老派西装，金表链",
        "outfit_ancient": "褐色锦衣，铜扣腰带",
        "vibe": "池家长辈，利益导向",
    },
}

_FACE_POOL_F = (
    "鹅蛋脸、桃花眼上挑、高鼻薄唇、颧骨利落",
    "圆润脸、杏眼含笑、小巧鼻、粉润柔唇",
    "瓜子脸、丹凤眼、挺鼻、唇峰明显",
    "方圆脸、细长眼、鼻翼柔、唇色淡",
    "长脸、下垂眼、高鼻、薄唇冷静",
)
_FACE_POOL_M = (
    "剑眉星目、高鼻薄唇、下颌线锋利",
    "浓眉深目、鼻梁挺、方颌有胡青",
    "短眉细眼、鼻头微圆、嘴角常挂玩味",
    "宽眉厚唇、颧骨高、国字脸",
    "清秀单眼皮、鼻梁直、下颌收束",
)
_HAIR_POOL_F = (
    "黑色大波浪长发",
    "黑色长直发中分",
    "棕色齐肩微波浪",
    "黑色高马尾利落",
    "盘发露颈带耳饰",
)
_HAIR_POOL_M = (
    "黑色短发侧分",
    "背头油光短发",
    "微乱痞帅短发",
    "寸发利落",
    "中长微卷及耳",
)
_OUTFIT_MODERN_F = (
    "修身西装套裙+细高跟",
    "丝质吊带裙外搭小香风外套",
    "高领针织+阔腿裤+耳环",
    "运动外套+短裙潮牌感",
    "旗袍改良裙+细跟凉鞋",
)
_OUTFIT_MODERN_M = (
    "深色三件套西装+领带",
    "黑色皮衣+白T+烟管裤",
    "羊绒大衣+高领毛衣",
    "休闲西装配衬衫敞领",
    "运动夹克+工装裤潮男",
)
_OUTFIT_ANCIENT_F = (
    "绯红织金长裙外罩薄纱",
    "月白罗裙银簪",
    "墨绿交领襦裙",
    "淡紫齐胸襦裙金步摇",
    "玄色劲装女装束发",
)
_OUTFIT_ANCIENT_M = (
    "玄色锦袍玉带",
    "青色长衫布靴",
    "绯红官袍",
    "劲装短打外罩氅衣",
    "灰褐布衣腰刀",
)


def _name_pick(name: str, pool: Tuple[str, ...], salt: int = 0) -> str:
    if not pool:
        return ""
    h = sum(ord(ch) for ch in (name or "角色")) + int(salt)
    return pool[h % len(pool)]


def _infer_gender_age(
    name: str, role_note: str = "", lines: Optional[List[str]] = None, actions: Optional[List[str]] = None
) -> Tuple[str, str]:
    blob = f"{name} {role_note} {' '.join(lines or [])} {' '.join(actions or [])}"
    if any(k in blob for k in ("父亲", "爸", "家主", "老爷", "伯父", "叔叔", "中年男")):
        return "男", "50岁上下中年男性"
    if any(k in blob for k in ("母亲", "妈", "夫人", "阿姨", "中年女", "贵妇")):
        return "女", "48岁上下中年女性"
    if any(k in blob for k in ("总裁", "少爷", "哥哥", "弟弟", "男主", "男配", "先生")):
        return "男", "28岁上下成年男性"
    if any(k in blob for k in ("小姐", "姐姐", "妹妹", "女主", "女配", "姑娘")):
        return "女", "23岁上下年轻女性"
    # 名字启发式
    fem_chars = set("月然丽慧媛婷芳娟娜玲红梅霞燕淑珍琴")
    male_chars = set("胜城建雄昌伟强军峰磊涛斌浩凯")
    if name and name[-1] in fem_chars:
        return "女", "24岁上下年轻女性"
    if name and name[-1] in male_chars:
        return "男", "27岁上下成年男性"
    if any(k in name for k in ("小姐", "夫人", "姑娘", "姐", "妹", "娘")):
        return "女", "24岁上下年轻女性"
    if any(k in name for k in ("少爷", "先生", "哥", "弟", "叔", "伯")):
        return "男", "28岁上下成年男性"
    return "中性", "青年"


def _build_distinct_character_appearance(
    *,
    name: str,
    role_note: str = "",
    lines: Optional[List[str]] = None,
    actions: Optional[List[str]] = None,
    era: str = "modern",
    used_sigs: Optional[set] = None,
) -> Tuple[str, str]:
    """返回 (appearance正文, role_note补强)。保证同项目角色脸型/发型/服装签名不重复。"""
    used = used_sigs if used_sigs is not None else set()
    anchor = _CAST_LOOK_ANCHORS.get(name)
    if anchor:
        gender = anchor["gender"]
        age = anchor["age"]
        face = anchor["face"]
        hair = anchor["hair"]
        body = anchor["body"]
        outfit = anchor["outfit_modern"] if era == "modern" else anchor["outfit_ancient"]
        vibe = anchor["vibe"]
        note = (
            f"{name}，{age}，{gender}。{vibe}。"
            f"{(role_note or '').strip()[:120]}"
        ).strip("。") + "。"
    else:
        gender, age = _infer_gender_age(name, role_note, lines, actions)
        # 尝试从 role_note 抽年龄词
        am = re.search(r"(\d{1,2})\s*岁", role_note or "")
        if am:
            age = f"{am.group(1)}岁{'女性' if gender=='女' else '男性' if gender=='男' else '青年'}"
        is_f = gender == "女" or (gender == "中性" and _name_pick(name, ("a", "b")) == "a")
        face_pool = _FACE_POOL_F if is_f else _FACE_POOL_M
        hair_pool = _HAIR_POOL_F if is_f else _HAIR_POOL_M
        if era == "modern":
            outfit_pool = _OUTFIT_MODERN_F if is_f else _OUTFIT_MODERN_M
        else:
            outfit_pool = _OUTFIT_ANCIENT_F if is_f else _OUTFIT_ANCIENT_M
        # 去撞脸：轮换 salt
        face = hair = outfit = ""
        for salt in range(len(face_pool) * 2):
            face = _name_pick(name, face_pool, salt)
            hair = _name_pick(name, hair_pool, salt + 3)
            outfit = _name_pick(name, outfit_pool, salt + 7)
            # 修 ancient pool 里误写的 saturation
            outfit = outfit.replace(" saturation: ", "").strip()
            sig = f"{face}|{hair}|{outfit}"
            if sig not in used:
                used.add(sig)
                break
        body = "高挑匀称" if is_f else "肩宽挺拔"
        if "中年" in age:
            body = "体态端庄微丰" if is_f else "微胖敦实有威"
        vibe = (actions[0] if actions else "")[:40] or "神态明确、可辨识"
        gender = "女" if is_f else "男"
        note = (
            f"{name}，{age}。性格气质：{vibe}。"
            f"{(role_note or '').strip()[:100]}"
        ).strip("。") + "。"

    era_bit = "现代都市时装" if era == "modern" else "古装服饰"
    appearance = (
        f"{name}，{age}，{gender}性角色；"
        f"五官：{face}；发型：{hair}；体态：{body}；"
        f"服装（{era_bit}）：{outfit}；"
        f"辨识要点必须与同剧其他角色明显不同：脸型、眉眼、发型长度/颜色倾向、服装色系与版型均不可雷同；"
        f"禁止所有角色共用同一张脸或同一套衣服。"
    )
    sig = f"{face}|{hair}|{outfit}"
    used.add(sig)
    return appearance[:420], note[:420]


def _infer_voice_profile(name: str, lines: List[str], actions: List[str]) -> str:
    """参考「文字音色」写法：音域+质感+习惯+情绪变化，不以名字开头。"""
    female = ("小姐", "姑娘", "夫人", "母亲", "姐姐", "妹妹", "娘", "珠", "氏", "亭", "若", "柔")
    male = ("少爷", "侯爷", "公子", "父亲", "兄", "弟", "王", "将军", "首领", "道成", "暗卫")
    group = ("宾客", "护院", "众人", "家丁", "丫鬟", "婆子", "侍卫", "群")
    child = ("锦禾", "团团", "阿杏", "阿福", "童", "娃")
    blob = name + "。" + "。".join(actions[:2]) + "。" + "。".join(lines[:2])

    if any(h in name for h in group):
        return "群像成年男女声线，层次丰富；附和讥讽时带轻蔑，危机时转为惊慌求生。"
    if any(h in name for h in child):
        # 勿写「语速偏慢/平稳」：H3 会拖长字音把短句撑满整段
        return "幼童女声，细软带鼻音，自然语速咬字清楚，不拖长字音；好奇时略扬，委屈时尾音轻收。"
    if any(h in name for h in female) or name.endswith("氏"):
        base = "年轻女性中高音，清亮偏冷，咬字利落，自然语速"
        if "珠" in name or "明珠" in name:
            base = "细软带鼻音的年轻女声，自然语速，尾音委屈"
        elif "氏" in name or "夫人" in name:
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
        # 禁止写入「参考『台词』」样例：成片模型会当成额外对白念出，造成多声/怪声
        return "中性声线，自然语速，咬字清楚；语气随剧情在克制与压迫间切换，不拖长字音。"
    return "中性声线，自然语速，咬字清楚；情绪不夸张，重点句略压低，不拖长字音。"


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

    # 先判时代：都市现造 vs 古装（避免现代剧被套成古装同一张脸）
    era_blob = " ".join(
        [
            art_style or "",
            *[
                f"{(c.get('name') or '')} {(c.get('role_note') or '')} "
                f"{' '.join(c.get('_actions') or [])} {' '.join(c.get('_lines') or [])}"
                for c in (assets.get("characters") or [])
            ],
            *[
                f"{(s.get('name') or '')} {(s.get('role_note') or '')}"
                for s in (assets.get("scenes") or [])
            ],
        ]
    )
    story_era = _detect_story_era(era_blob)
    used_look_sigs: set = set()

    for c in assets.get("characters") or []:
        name = (c.get("name") or "角色").strip()
        lines = c.get("_lines") or []
        actions = c.get("_actions") or []
        eps = c.get("episodes") or []
        ep_txt = "、".join(f"第{int(x)}集" for x in eps[:6]) if eps else "多集"

        appearance, look_note = _build_distinct_character_appearance(
            name=name,
            role_note=c.get("role_note") or "",
            lines=lines,
            actions=actions,
            era=story_era,
            used_sigs=used_look_sigs,
        )

        if force or _is_weak_text(c.get("role_note") or "", name, min_len=18):
            act = actions[0] if actions else ""
            # 禁止写入「如『台词』」样例：成片模型会当成额外对白念出
            line_hint = "对白干净利落，咬字清楚。" if lines else ""
            if act:
                c["role_note"] = (
                    f"{look_note}出场于{ep_txt}。剧中可见：{act[:70]}。{line_hint}"
                )[:420]
            else:
                c["role_note"] = (
                    f"{look_note}出场于{ep_txt}。"
                    f"{line_hint or '神态与行动明确，外貌按年龄身份区分。'}"
                )[:420]

        if force or _is_weak_text(c.get("visual_prompt") or "", name, min_len=20):
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



def llm_extract_assets_from_novel(
    novel: str,
    episodes: List[Dict[str, Any]],
    *,
    art_style: str = "半写实3D国漫",
    model: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """无 LLM 从整本/分集剧本抽取角色、场景、道具（用于非 SCRIPTSET 网文等格式）。"""
    sample_eps = []
    for e in (episodes or [])[:6]:
        body = (e.get("script_md") or "")[:1200]
        if body.strip():
            sample_eps.append(
                f"### 第{int(e.get('ep_num') or 0):03d}集｜{e.get('title') or ''}\n{body}"
            )
    # 单集超长网文：只取前 8k，避免 LLM 超时拖死解析
    blob = "\n\n".join(sample_eps) if sample_eps else (novel or "")[:8000]
    if not blob.strip():
        return {"characters": [], "scenes": [], "props": []}
    messages = [
        {
            "role": "system",
            "content": (
                "你是漫剧资产拆解助手。从剧本中识别主要角色、场景、关键道具，输出严格 JSON（不要 markdown）："
                '{"characters":[{"name":"角色名","role_note":"身份性格外貌简述","episodes":[1,2],'
                '"voice_desc":"音色简述","visual_prompt":"外表要点"}],'
                '"scenes":[{"name":"场景名","role_note":"空间氛围","episodes":[1],'
                '"visual_prompt":"空间要点"}],'
                '"props":[{"name":"道具名","role_note":"剧情作用","episodes":[1],'
                '"visual_prompt":"外形要点"}]}'
                "硬规则：角色名必须是人名/称呼（如陈野、张嘉乐），严禁把「冷声/汇报/赶紧/轻轻一笑/工作人员」"
                "等动作、语气、职务集合词当成角色；最多角色12、场景10、道具8。"
            ),
        },
        {
            "role": "user",
            "content": f"画风偏好：{art_style}\n\n剧本内容：\n{blob}",
        },
    ]
    raw = chat_completion(
        messages,
        model=script_model(model),
        temperature=0.2,
        max_tokens=2200,
        timeout=45.0,
    )
    data = _extract_json(raw) or {}
    out: Dict[str, List[Dict[str, Any]]] = {
        "characters": [],
        "scenes": [],
        "props": [],
    }
    for c in data.get("characters") or []:
        name = _clean_char_name(c.get("name") or "")
        if not name:
            continue
        eps = c.get("episodes") or []
        eps_i = sorted(
            {int(x) for x in eps if str(x).isdigit() or isinstance(x, int)}
        )
        out["characters"].append(
            {
                "name": name,
                "role_note": (c.get("role_note") or "").strip(),
                "visual_prompt": (c.get("visual_prompt") or "").strip(),
                "voice_desc": (c.get("voice_desc") or "").strip(),
                "episodes": eps_i,
                "_lines": [],
                "_actions": [],
                "_hits": 3,
            }
        )
    for s in data.get("scenes") or []:
        name = (s.get("name") or s.get("code") or "").strip()
        if not name:
            continue
        eps = s.get("episodes") or []
        eps_i = sorted(
            {int(x) for x in eps if str(x).isdigit() or isinstance(x, int)}
        )
        out["scenes"].append(
            {
                "code": (s.get("code") or "").strip(),
                "name": name,
                "visual_prompt": (s.get("visual_prompt") or "").strip(),
                "role_note": (s.get("role_note") or "").strip(),
                "episodes": eps_i,
                "_actions": [],
            }
        )
    for p in data.get("props") or []:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        eps = p.get("episodes") or []
        eps_i = sorted(
            {int(x) for x in eps if str(x).isdigit() or isinstance(x, int)}
        )
        out["props"].append(
            {
                "name": name,
                "visual_prompt": (p.get("visual_prompt") or "").strip(),
                "role_note": (p.get("role_note") or "").strip(),
                "episodes": eps_i,
                "_ctx": [],
            }
        )
    return fill_asset_descriptions(out, art_style=art_style)


def _merge_asset_packs(
    base: Dict[str, List[Dict[str, Any]]],
    extra: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """按名称合并资产包，保留 base 优先字段，补全空描述与集数。"""
    out: Dict[str, List[Dict[str, Any]]] = {
        "characters": [],
        "scenes": [],
        "props": [],
    }
    for kind in ("characters", "scenes", "props"):
        by_name: Dict[str, Dict[str, Any]] = {}
        for item in (base.get(kind) or []) + (extra.get(kind) or []):
            name = (item.get("name") or "").strip()
            if not name:
                continue
            if name not in by_name:
                by_name[name] = dict(item)
                continue
            cur = by_name[name]
            for f in ("role_note", "visual_prompt", "voice_desc", "code"):
                if not (cur.get(f) or "").strip() and (item.get(f) or "").strip():
                    cur[f] = item.get(f)
            eps = set(cur.get("episodes") or []) | set(item.get("episodes") or [])
            cur["episodes"] = sorted(
                {int(x) for x in eps if str(x).isdigit() or isinstance(x, int)}
            )
            for f in ("_lines", "_actions", "_ctx"):
                if f in item or f in cur:
                    merged = list(cur.get(f) or [])
                    for x in item.get(f) or []:
                        if x not in merged:
                            merged.append(x)
                    cur[f] = merged[:8]
        out[kind] = list(by_name.values())
    return out


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

    # 角色分批（最多 12 人、每批 6），超时缩短，避免解析页一直转圈
    chars = list(assets.get("characters") or [])[:12]
    for i in range(0, len(chars), 6):
        batch = chars[i : i + 6]
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
                    '{"characters":[{"name":"","role_note":"身份+年龄段+性格120字内勿只写姓名",'
                    '"voice_desc":"音域音质语速习惯40字内勿以姓名开头如：中年男性中低音，威严厚重…",'
                    '"visual_prompt":"必须含：角色外表描述：…（年龄、性别、脸型五官、发型发色、体态、服装色系版型材质、鞋子配饰）\\n风格描述：'
                    + art_style
                    + "…\\n"
                    '图片展示结构：单张横向16:9组合图，左正面无头全身+左背面有头全身+右正面胸像放大…"}]}'
                    "硬性：每个角色必须有可区分的年龄段与外貌签名；禁止多人共用同一张脸、同一发型、同一套衣服；"
                    "若剧本是现代都市（手机/公司/赛车/西装等），服装必须现代时装，严禁古装；"
                    "若是古装架空才写古装。姐妹/兄弟也要脸型眉眼发型明显不同。"
                    "每个状态/造型也必须按同一三视图结构出图，不可只出单角度。"
                    "visual_prompt 要足够长以约束服饰材质与三视图；必须覆盖名单中每个 name，只输出 JSON。"
                    "风格描述必须使用本剧画风「"
                    + art_style
                    + "」，不要改成其他画风。"
                    "若 name 明显不是人名（如冷声、汇报、赶紧、梗概、OS），跳过该项。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            raw = chat_completion(
                messages,
                model=script_model(model),
                temperature=0.4,
                max_tokens=1800,
                timeout=40.0,
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
            for s in (assets.get("scenes") or [])[:12]
        ],
        "props": [
            {"name": p.get("name"), "draft": (p.get("visual_prompt") or "")[:80]}
            for p in (assets.get("props") or [])[:10]
        ],
        "novel_excerpt": excerpt[:3000],
    }
    messages2 = [
        {
            "role": "system",
            "content": (
                "你是漫剧场景道具设计师。输出 JSON："
                '{"scenes":[{"name":"","role_note":"场景叙事说明40-80字勿等于名称",'
                '"visual_prompt":"须含：场景外表描述：…\\n风格描述：'
                + art_style
                + "…\\n图片展示结构：单张横向16:9三视图母景组合…\"}],"
                '"props":[{"name":"","role_note":"资产说明40字内写材质用途勿等于名称",'
                '"visual_prompt":"须含：道具外表描述：…\\n风格描述：'
                + art_style
                + "…\\n图片展示结构：单张横向16:9正背+细节特写…\"}]}"
                "覆盖全部名单，只输出 JSON。role_note 绝不能与 name 相同。风格须为「"
                + art_style
                + "」。"
            ),
        },
        {"role": "user", "content": json.dumps(payload2, ensure_ascii=False)},
    ]
    try:
        raw2 = chat_completion(
            messages2,
            model=script_model(model),
            temperature=0.4,
            max_tokens=2200,
            timeout=40.0,
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
    segment_sec: int = 9,
) -> str:
    """按「场 → 分段」生成视频脚本。

    时长按内容：
    - 对白/动作充实 → 单段约 8–9s（说完用微表情承接，勿拆成 3–5s）；
    - 极短句/轻动作、易空声 → 3–5s；
    整集合计不得低于 60 秒。
    """
    art = summary.get("art_style") or "半写实3D国漫"
    ratio = summary.get("aspect_ratio") or "16:9"
    # P 段标题用短集号，避免把整段梗概塞进每段标题
    title = f"第{int(ep.get('ep_num') or 1):03d}集"
    body = (ep.get("script_md") or "").strip()
    # 拆段时的「对白字数预算」参考时长（充实段按 ~8–9s 估）
    target_sec = max(7, min(10, int(segment_sec or 9)))
    episode_min_sec = 60
    # 充实段偏 8–9s，约 7–12 段即可到 60s+；勿为凑段数把完整内容拆碎
    max_segments = 16
    min_segments = 7

    # 按场景块拆分（场）。支持：
    # - EP001-SC01｜外·主城广场·日
    # - 场景1-2 外景·明玉女学门前·日
    # - 短剧：1-1 赛车场 夜 外
    # - 标题行内嵌「第001集｜xxx 场景1-1 外景·…」
    scene_blocks: List[Tuple[str, List[str]]] = []
    cur_name: Optional[str] = None
    cur_lines: List[str] = []

    def _flush_scene() -> None:
        nonlocal cur_name, cur_lines
        if cur_name is not None and cur_lines:
            scene_blocks.append((cur_name, cur_lines))
        cur_name = None
        cur_lines = []

    def _scene_name_from_num_line(s: str) -> Optional[str]:
        m = SCENE_NUM_RE.match(s)
        if m:
            # 名称优先用地点描述；保留编号便于对照
            place = (m.group(3) or "").strip()
            return place or f"场景{m.group(1)}-{m.group(2)}"
        # 短剧：1-1 赛车场 夜 外
        m_short = SHORT_SCENE_RE.match(s)
        if m_short:
            return _normalize_short_scene_name(m_short.group(3) or "") or None
        # 行中内嵌「场景1-1 外景·…」（常见于集标题同行）
        m2 = re.search(
            r"场景\s*(\d+)\s*[-–—]\s*(\d+)\s+([^\n]{2,40})$",
            s,
        )
        if m2:
            place = (m2.group(3) or "").strip()
            return place or f"场景{m2.group(1)}-{m2.group(2)}"
        return None

    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            continue
        # 梗概 / 人：出场表 不当正文，也不开「本集场景」假场
        if _is_script_preamble_line(s) and cur_name is None:
            continue
        if PEOPLE_RE.match(s) or CAST_LINE_RE.match(s) or re.match(r"^梗概\s*[：:]", s):
            continue
        sm = SCENE_RE.match(s)
        if sm:
            _flush_scene()
            cur_name = sm.group(2).strip() or sm.group(1).strip()
            cur_lines = []
            continue
        sn = _scene_name_from_num_line(s)
        if sn:
            _flush_scene()
            cur_name = sn
            cur_lines = []
            # 纯场标行（场景N-M / 1-1 赛车场 夜 外）本身不进正文
            if SCENE_NUM_RE.match(s) or SHORT_SCENE_RE.match(s):
                continue
            # 标题行：去掉「场景…」后缀后若还有有效内容则保留
            rest = re.sub(
                r"\s*场景\s*\d+\s*[-–—]\s*\d+\s+[^\n]{2,40}\s*$",
                "",
                s,
            ).strip()
            if rest and not rest.startswith("第") and not _is_script_preamble_line(rest):
                cur_lines.append(rest)
            continue
        if cur_name is None:
            # 尚未碰到场景标：暂存到默认场，避免丢掉开场对白
            cur_name = "本集场景"
        if s:
            cur_lines.append(s)
    _flush_scene()
    # 若唯一场名叫「本集场景」且剧本其实有场景标，上面已拆；仅完全无标时保留
    if not scene_blocks:
        fallback_lines = [
            ln.strip()
            for ln in body.splitlines()
            if ln.strip()
            and not _is_script_preamble_line(ln.strip())
            and not SHORT_SCENE_RE.match(ln.strip())
            and not SCENE_NUM_RE.match(ln.strip())
        ]
        scene_blocks = [("本集场景", fallback_lines or ["（空剧本）"])]
    # 去掉空场；丢弃仅含梗概/出场表的假「本集场景」
    scene_blocks = [(n, ls) for n, ls in scene_blocks if ls]
    if len(scene_blocks) >= 2 and scene_blocks[0][0] == "本集场景":
        first_lines = scene_blocks[0][1]
        real_lines = [
            x
            for x in first_lines
            if not _is_script_preamble_line(x)
            and not PEOPLE_RE.match(x)
            and not re.match(r"^梗概\s*[：:]", x)
        ]
        has_real_dlg = any(
            DIALOGUE_RE.match(x)
            and (DIALOGUE_RE.match(x).group(1) or "").strip()
            not in ("梗概", "人", "人物", "场景")
            for x in real_lines
        )
        if not has_real_dlg:
            scene_blocks = scene_blocks[1:]

    def _anchor_desc(name: str, note: str, fallback: str) -> str:
        d = (note or "").strip()
        if not d or d == name or d == f"是{name}。" or d.startswith(f"是{name}"):
            return fallback
        # 去掉角色卡里的台词样例，避免进入一致性锁定后被念出
        d = re.sub(
            r"对白干净利落[，,]?如\s*[「『\"“][^」』\"”]{0,80}[」』\"”]\s*[。．.]?",
            "对白干净利落。",
            d,
        )
        d = re.sub(r"如\s*[「『\"“][^」』\"”]{1,60}[」』\"”]", "", d)
        d = d.replace("「", "『").replace("」", "』")
        return d[:96]

    def _lines_to_beats(lines: List[str], scene_name: str) -> List[Dict[str, str]]:
        beats: List[Dict[str, str]] = []
        _skip_roles = {
            "结尾钩子",
            "钩子",
            "人物",
            "人",
            "梗概",
            "场景",
            "旁白",
            "画外音",
            "OS",
        }
        for s in lines:
            if not s or _is_script_preamble_line(s):
                continue
            if PEOPLE_RE.match(s) or CAST_LINE_RE.match(s):
                continue
            if SHORT_SCENE_RE.match(s) or SCENE_NUM_RE.match(s) or SCENE_RE.match(s):
                continue
            if s.startswith("人物") or s.startswith("人：") or s.startswith("人:"):
                continue
            # 结尾钩子/集末提示不是角色对白
            if re.match(r"^(?:结尾钩子|钩子|本集完|未完待续)\s*[：:]", s):
                tip = re.sub(r"^[^：:]+[：:]\s*", "", s).strip()
                if tip:
                    beats.append({"type": "action", "text": tip})
                continue
            if re.match(r"^(?:旁白|画外音|OS)\s*[：:]", s):
                tip = re.sub(r"^[^：:]+[：:]\s*", "", s).strip()
                if tip:
                    beats.append({"type": "dialogue", "role": "旁白", "text": tip})
                continue
            # 白晓月 OS：… → 内心独白
            m_os = re.match(
                r"^[ \t　]*([\u4e00-\u9fffA-Za-z0-9·]{1,16})\s+OS\s*[：:]\s*(.+)$",
                s,
            )
            if m_os:
                beats.append(
                    {
                        "type": "dialogue",
                        "role": (m_os.group(1) or "").strip() + "（OS）",
                        "text": (m_os.group(2) or "").strip(),
                    }
                )
                continue
            if s.startswith("▲") or s.startswith("△"):
                beats.append({"type": "action", "text": s.lstrip("▲△").strip()})
                continue
            # 【同镜，分屏】等镜头指示 → 动作提示
            if s.startswith("【") and s.endswith("】"):
                beats.append({"type": "action", "text": s.strip("【】")})
                continue
            m = DIALOGUE_RE.match(s)
            if m:
                role = m.group(1).strip()
                if role in _skip_roles:
                    if role in ("旁白", "画外音", "OS"):
                        beats.append(
                            {
                                "type": "dialogue",
                                "role": "旁白",
                                "text": (m.group(2) or "").strip(),
                            }
                        )
                    else:
                        tip = (m.group(2) or "").strip()
                        if tip and role not in ("梗概", "人", "人物"):
                            beats.append({"type": "action", "text": tip})
                    continue
                beats.append(
                    {
                        "type": "dialogue",
                        "role": role,
                        "text": (m.group(2) or "").strip(),
                    }
                )
            elif s and not s.startswith("第") and "｜" not in s[:8]:
                beats.append({"type": "action", "text": s})
        if not beats:
            beats = [{"type": "action", "text": f"{scene_name}定场"}]
        return beats

    def _pack_beats(beats: List[Dict[str, str]], n_shots: int) -> List[Dict[str, str]]:
        """少合并台词：优先一段一镜一句；允许单镜短段，避免硬凑留白。"""
        n_shots = max(1, min(3, n_shots))
        if not beats:
            return [{"type": "action", "text": "关节点睛动作，节奏利落"}]
        dlg_beats = [b for b in beats if b.get("type") == "dialogue"]
        act_beats = [b for b in beats if b.get("type") == "action"]
        act_prefix = "；".join(
            (a.get("text") or "").strip() for a in act_beats if (a.get("text") or "").strip()
        )[:120]
        # 一句对白 → 只出一镜；动作并入画面前缀，禁止「无/有台词/无」三镜让人以为多段同句
        if len(dlg_beats) == 1 and n_shots == 1:
            b0 = dict(dlg_beats[0])
            t = (b0.get("text") or "").strip()
            b0["text"] = t
            if act_prefix:
                b0["_action_prefix"] = act_prefix
            return [b0]
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
        # 允许单节拍短段，禁止为凑镜数硬加留白/慢动作
        if not items:
            items.append({"type": "action", "text": "关节点睛动作，节奏利落"})
        # 台词必须保留剧本原文，禁止按逗号截成「白晓月，」这种残句；
        # 字多就靠加长本段时长（上限 10 秒），不要改字。
        out: List[Dict[str, str]] = []
        for b in items[:n_shots]:
            if b.get("type") == "dialogue":
                t = (b.get("text") or "").strip()
                # 仅极端超长（>48 字）才在句号/问叹号处拆，绝不在逗号处截断
                if _dialogue_char_count(t) > 48:
                    cut = t
                    for sep in ("。", "！", "？", "；"):
                        if sep in t:
                            head = (t.split(sep)[0] + sep).strip()
                            if 12 <= _dialogue_char_count(head) <= 48:
                                cut = head
                                break
                    item = {**b, "text": cut}
                else:
                    item = {**b, "text": t}
                if act_prefix and len(dlg_beats) == 1:
                    item["_action_prefix"] = act_prefix
                out.append(item)
            else:
                out.append(b)
        return out

    def _n_shots_for(beats: List[Dict[str, str]]) -> int:
        n_dlg = sum(1 for b in beats if b.get("type") == "dialogue")
        if n_dlg == 1:
            return 1
        if n_dlg <= 0:
            return 1 if len(beats) <= 1 else min(2, max(1, len(beats)))
        return min(3, n_dlg)

    def _segment_assets(beats: List[Dict[str, str]], scene_name: str) -> Tuple[List[str], List[str]]:
        """只锁定本段实际用到的场景/角色/道具，禁止把全剧资产塞进一致性锁定。"""
        _meta_names = {
            "结尾钩子",
            "钩子",
            "旁白",
            "画外音",
            "OS",
            "人物",
            "场景",
            "本集场景",
        }
        _generic_scene_bits = {
            "外景",
            "内景",
            "日",
            "夜",
            "午后",
            "清晨",
            "黄昏",
            "上午",
            "阴天",
            "傍晚",
            "深夜",
        }
        roles = {
            (b.get("role") or "").strip()
            for b in beats
            if b.get("type") == "dialogue" and (b.get("role") or "").strip()
        }
        roles -= _meta_names
        blob = scene_name + "。" + "。".join(
            (b.get("role") or "") + (b.get("text") or "") for b in beats
        )
        anchors: List[str] = []

        # 场景：只挂本段场景名（完整匹配）；禁止用「外景/内景」碎片误匹配全剧
        scene_hit = None
        for s in scenes:
            n = (s.get("name") or s.get("code") or "").strip()
            if not n:
                continue
            if n == scene_name or n in scene_name or scene_name in n:
                scene_hit = s
                break
        if scene_hit is None:
            for s in scenes:
                n = (s.get("name") or "").strip()
                if n and n in blob and n == scene_name:
                    scene_hit = s
                    break
        if scene_hit is not None:
            n = (scene_hit.get("name") or scene_name).strip()
            anchors.append(
                f"@{n}："
                + _anchor_desc(
                    n,
                    scene_hit.get("role_note") or scene_hit.get("visual_prompt") or "",
                    f"{n}场景母景，空镜定场，光影统一",
                )
            )
        else:
            anchors.append(
                f"@{scene_name}："
                + _anchor_desc(scene_name, "", f"{scene_name}场景母景，空镜定场，光影统一")
            )

        for c in characters:
            n = (c.get("name") or "").strip()
            if not n or n in _meta_names:
                continue
            # 必须是本段说话人，或动作文案完整点名（避免短词误伤）
            if n in roles or (len(n) >= 2 and n in blob):
                anchors.append(
                    f"@{n}："
                    + _anchor_desc(n, c.get("role_note") or "", f"{n}角色形象，五官清晰，服饰稳定")
                )
        for p in props:
            n = (p.get("name") or "").strip()
            if n and len(n) >= 2 and n in blob:
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
            # 再挡一次：场景锚点名若只是「外景」这类，丢弃
            bare = key.lstrip("@")
            if bare in _generic_scene_bits:
                continue
            seen.add(key)
            uniq.append(a)
        voices = []
        try:
            from .drama10_export import strip_voice_reference_samples
        except Exception:
            strip_voice_reference_samples = lambda x: (x or "").strip()  # type: ignore
        for c in characters:
            n = (c.get("name") or "").strip()
            if not n or n in _meta_names or n not in roles:
                continue
            vd = strip_voice_reference_samples((c.get("voice_desc") or "").strip())
            if vd and vd != n and not vd.startswith(f"是{n}"):
                voices.append(f"🔊 @{n} · 音色：{vd}")
            else:
                voices.append(f"🔊 @{n} · 音色：中性偏冷声线，咬字清楚，情绪随剧情切换。")
        return uniq[:8], voices[:6]

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

    def _content_duration_sec(packed: List[Dict[str, str]]) -> int:
        """按时长贴内容：充实对白约 8–9s；极短/易空声才 3–5s。"""
        from .drama10_export import _estimate_speak_sec

        speak = 0.0
        n_dlg = 0
        chars_n = 0
        for b in packed:
            if b.get("type") == "dialogue":
                n_dlg += 1
                t = b.get("text") or ""
                speak += _estimate_speak_sec(t)
                chars_n += _dialogue_char_count(t)
        if n_dlg:
            need = speak + 0.8
            # 内容充实：落在 8–9s（说完后微表情/走动填满，禁止拆成 3–5s）
            if speak >= 4.0 or chars_n >= 14:
                return int(max(8, min(10, round(max(need, 8.5)))))
            if speak >= 2.2 or chars_n >= 8:
                return int(max(6, min(9, round(max(need, 7)))))
            # 极短句：3–5s，避免空声硬撑
            return int(max(3, min(5, round(max(need, 3)))))
        # 纯动作：轻内容 3–5s；多拍动作可到 6–8s
        if len(packed) >= 2:
            return int(max(6, min(8, 4 + len(packed))))
        return int(max(3, min(5, 2 + max(1, len(packed)))))

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
        floor_dur: int = 0,
    ) -> Tuple[str, str]:
        # 一句对白只拆一镜；禁止「无台词镜 + 台词镜」并排造成两段像同一句
        n_want = _n_shots_for(beats)
        packed = _pack_beats(beats, n_shots=n_want)
        dur = _content_duration_sec(packed)
        # 整集补足到 ≥60s 时的段内下限（仍封顶 10s，用反应/走动填，不静默）
        if floor_dur and int(floor_dur) > dur:
            dur = int(max(dur, min(10, int(floor_dur))))
        n_lens = max(1, len(packed))
        base = max(1, dur // n_lens)
        rem = dur - base * n_lens
        durs = [base] * n_lens
        for i in range(max(0, rem)):
            durs[i % len(durs)] += 1
        # 对白镜至少够说完（单镜最短 2s，整段仍 ≥3）
        from .drama10_export import _estimate_speak_sec

        for i, b in enumerate(packed):
            if b.get("type") == "dialogue":
                need_i = int(max(2, min(8, round(_estimate_speak_sec(b.get("text") or "") + 0.5))))
                if durs[i] < need_i:
                    durs[i] = need_i
        dur = int(max(3, min(10, sum(durs))))
        # 若加长后超 10s，按比例压回
        while sum(durs) > 10 and any(x > 1 for x in durs):
            for i in range(len(durs)):
                if durs[i] > 1 and sum(durs) > 10:
                    durs[i] -= 1
        dur = int(max(3, min(10, sum(durs))))
        # 再次贴齐 floor（整集预算）
        if floor_dur and int(floor_dur) > dur:
            grow = min(10, int(floor_dur)) - dur
            if grow > 0:
                durs[-1] = min(10, durs[-1] + grow)
                dur = int(max(3, min(10, sum(durs))))
                while sum(durs) > 10 and any(x > 1 for x in durs):
                    for i in range(len(durs)):
                        if durs[i] > 1 and sum(durs) > 10:
                            durs[i] -= 1
                dur = int(max(3, min(10, sum(durs))))
        stretched = bool(floor_dur and int(floor_dur) >= dur and dur > _content_duration_sec(packed))

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
        light_line = _scene_lighting_clause(scene_name, title)
        head = (
            f"[风格]\n{art}\n画幅：{ratio}\n\n"
            f"[全局光影氛围]\n{art}，本段「{title}·{scene_name}」与同场其它分段共用一套光影色温；"
            f"{light_line or '人物面部高光可读，明暗有层次。'}；无背景音乐。\n\n"
            f"[场景母版]\n{master}\n\n"
            f"[站位轴]\n{blocking}\n\n"
            f"[一致性锁定]\n"
            + ("\n".join(anchors) if anchors else "（暂无资产锚定）")
            + ("\n" + "\n".join(voices) if voices else "")
            + "\n\n[负面排除]\n无背景音乐；无额外字幕；无水印；"
            "允许正脸说话；禁止每段换场景；禁止人物左右站位对调；"
            "禁止木桩站桩、表情定格、双手僵垂、拍照感；禁止平光死白或死黑糊脸；"
            "禁止慢动作拖沓、静默空白、为凑时长空镜发呆；"
            "下一段必须从本段结束姿态接着演，禁止重新摆位、下跪或换机位翻面。\n\n"
        )

        t0 = 0
        shot_blocks = []
        last_pos = (prev_handoff or "").strip() or blocking
        cam = "中近景稳机跟拍，节奏利落，禁止慢动作拖沓，不越轴、不翻面。"
        # 本段动作原文（△…），写进画面，避免只剩模板句看不到剧本描写
        action_notes = "；".join(
            (x.get("text") or "").strip()
            for x in packed
            if x.get("type") == "action" and (x.get("text") or "").strip()
        )
        for si, b in enumerate(packed, start=1):
            d = durs[si - 1]
            t1 = t0 + d
            if b["type"] == "dialogue":
                speaker = (b.get("role") or "角色").strip()
                partner = _partner_for(speaker, roles)
                beat_act = (b.get("_action_prefix") or "").strip()
                act_prefix = f"{beat_act or action_notes}。" if (beat_act or action_notes) else ""
                # 画面勿反复粘贴整段站位轴；「说话」字样过多会诱发模型提前开嗓
                if partner:
                    visual = (
                        f"{act_prefix}{speaker}朝向{partner}通话/对谈；"
                        f"开嗓后口型与台词同步，可边走边说，说完立刻停口。"
                    )
                else:
                    visual = (
                        f"{act_prefix}{speaker}看向画内对象；"
                        f"开嗓后口型与台词同步，可边走边说，说完立刻停口。"
                    )
                if stretched:
                    visual += "说完后继续微表情、呼吸与走动承接至镜尾，环境声不中断，禁止静默发呆。"
                visual = _enrich_visual_lighting_motion(
                    visual,
                    scene_name=scene_name,
                    title=title,
                    has_dialogue=True,
                )
                line = f"{speaker}：“{(b.get('text') or '').strip()}”"
                handoff = (
                    f"结束姿态：{speaker}与{partner or '对谈对象'}仍保持本场左右站位、都按结束时的姿势站着，"
                    f"背景陈设与「{scene_name}」完全不变；下一段从此姿态接着演，禁止重摆。"
                )
            else:
                act = (b.get("text") or "").strip()
                visual = f"{scene_name}。{act}。人物尚未开口；动作干脆利落，禁止慢镜拖沓。"
                if stretched:
                    visual += "持续轻动作与环境声填满本段，禁止静默空白。"
                visual = _enrich_visual_lighting_motion(
                    visual,
                    scene_name=scene_name,
                    title=title,
                    has_dialogue=False,
                )
                line = "无。"
                handoff = f"动作结束，站位轴不变；场景仍为「{scene_name}」。"
            # 【位置承接】只写差异：段间承接或「同站位轴」，禁止再贴一整段站位锁定
            if si == 1 and prev_handoff:
                pos_line = f"段间承接：{prev_handoff} 不换景、不翻面。"
            elif si == 1:
                pos_line = "同[站位轴]，本镜不重摆。"
            else:
                pos_line = "承接上一镜站位，不换景、不翻面。"
            shot_blocks.append(
                f"镜头{si}（{t0:02d}:00—{t1:02d}:00，{d}秒）\n"
                f"【时间标识】\n{t0:02d}:00–{t1:02d}:00\n"
                f"【运镜】\n{cam}\n"
                f"【画面】\n{visual}\n"
                f"【位置承接】\n{pos_line}\n"
                f"【OS】\n无。\n"
                f"【画外音】\n无。\n"
                f"【音效】\n轻环境声铺满；有台词时自然语速，禁止静默空白。\n"
                # 台词写在同一行，避免前端/镜头解析漏读下一行
                f"【台词】{line}"
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

    def _merge_leading_action_only(
        src: List[Tuple[int, int, str, List[Dict[str, str]]]],
    ) -> List[Tuple[int, int, str, List[Dict[str, str]]]]:
        """纯动作开场并入下一段有对白的同场分段，避免 P001【台词】无、用户以为整场没台词。"""
        out: List[Tuple[int, int, str, List[Dict[str, str]]]] = []
        i = 0
        while i < len(src):
            scene_idx, _sk, name, beats = src[i]
            has_dlg = any(b.get("type") == "dialogue" for b in beats)
            if (
                not has_dlg
                and i + 1 < len(src)
                and src[i + 1][0] == scene_idx
                and any(b.get("type") == "dialogue" for b in src[i + 1][3])
            ):
                _nsi, _nsk, _nname, nbeats = src[i + 1]
                src[i + 1] = (_nsi, _nsk, _nname, list(beats) + list(nbeats))
                i += 1
                continue
            out.append(src[i])
            i += 1
        return out

    plan = _merge_leading_action_only(plan)

    def _renumber_plan(
        src: List[Tuple[int, int, str, List[Dict[str, str]]]],
    ) -> List[Tuple[int, int, str, List[Dict[str, str]]]]:
        counters: Dict[int, int] = {}
        out: List[Tuple[int, int, str, List[Dict[str, str]]]] = []
        for scene_idx, _, name, beats in src[:max_segments]:
            counters[scene_idx] = counters.get(scene_idx, 0) + 1
            out.append((scene_idx, counters[scene_idx], name, beats))
        return out

    def _seg_has_rich_dialogue(beats: List[Dict[str, str]]) -> bool:
        chars_n = sum(
            _dialogue_char_count(b.get("text") or "")
            for b in beats
            if b.get("type") == "dialogue"
        )
        return chars_n >= 8

    # 仅拆「多人开口」或「对白过长」；充实单人段保持完整，勿一律拆成 3–5s 碎段
    def _split_only_when_needed(
        src: List[Tuple[int, int, str, List[Dict[str, str]]]],
    ) -> List[Tuple[int, int, str, List[Dict[str, str]]]]:
        out: List[Tuple[int, int, str, List[Dict[str, str]]]] = []
        for scene_idx, _sk, name, beats in src:
            if not beats:
                continue
            # 一段内多句对白且非同一人 → 拆开（单人一句规则）
            roles = [
                (b.get("role") or "").strip()
                for b in beats
                if b.get("type") == "dialogue"
            ]
            uniq_roles = {r for r in roles if r}
            total_chars = sum(
                _dialogue_char_count(b.get("text") or "")
                for b in beats
                if b.get("type") == "dialogue"
            )
            must_split = len(uniq_roles) > 1 or total_chars > seg_budget + 8
            if must_split and len(beats) > 1:
                # 按对白切开，动作跟前一句
                buf: List[Dict[str, str]] = []
                last_role = ""
                for b in beats:
                    if b.get("type") == "dialogue":
                        role = (b.get("role") or "").strip()
                        if buf and (role != last_role or sum(
                            _dialogue_char_count(x.get("text") or "")
                            for x in buf
                            if x.get("type") == "dialogue"
                        ) >= seg_budget):
                            out.append((scene_idx, 0, name, buf))
                            buf = []
                        last_role = role
                        buf.append(b)
                    else:
                        buf.append(b)
                if buf:
                    out.append((scene_idx, 0, name, buf))
            else:
                out.append((scene_idx, 0, name, beats))
        return _renumber_plan(out)

    plan = _split_only_when_needed(plan)

    # 整集 <60s 时：优先把充实对白段拉到 8–9s；仍不够再插反应段（勿拆碎完整内容）
    def _ensure_episode_runtime(
        src: List[Tuple[int, int, str, List[Dict[str, str]]]],
    ) -> Tuple[
        List[Tuple[int, int, str, List[Dict[str, str]]]],
        List[int],
    ]:
        cur = list(src)
        while True:
            natural: List[int] = []
            for _si, _sk, _sn, beats in cur:
                n_want = _n_shots_for(beats)
                packed = _pack_beats(beats, n_shots=n_want)
                natural.append(_content_duration_sec(packed))
            floors = list(natural)
            deficit = episode_min_sec - sum(floors)
            if deficit <= 0:
                return _renumber_plan(cur), floors
            # 先给充实对白段加到 8–9s
            grew = False
            for i, beats in enumerate([x[3] for x in cur]):
                if deficit <= 0:
                    break
                if _seg_has_rich_dialogue(beats) and floors[i] < 9:
                    add = min(9 - floors[i], deficit)
                    if add > 0:
                        floors[i] += add
                        deficit -= add
                        grew = True
            if deficit <= 0:
                return _renumber_plan(cur), floors
            # 其余段最多加到 10s
            for i in range(len(floors)):
                if deficit <= 0:
                    break
                if floors[i] < 10:
                    add = min(10 - floors[i], deficit)
                    floors[i] += add
                    deficit -= add
                    grew = True
            if deficit <= 0:
                return _renumber_plan(cur), floors
            # 仍不够且未超段数上限：插反应承接段（短 4–5s），不拆原充实段
            if len(cur) >= max_segments:
                return _renumber_plan(cur), floors
            insert_at = len(cur)
            for i in range(len(cur) - 1, -1, -1):
                if any(b.get("type") == "dialogue" for b in cur[i][3]):
                    insert_at = i + 1
                    break
            scene_idx, _, name, _b = cur[max(0, insert_at - 1)]
            cur.insert(
                insert_at,
                (
                    scene_idx,
                    0,
                    name,
                    [
                        {
                            "type": "action",
                            "text": "听完后微表情反应、呼吸与重心微调，目光游移，环境声持续",
                        }
                    ],
                ),
            )
            if not grew and len(cur) >= max_segments:
                break
        # fallback
        natural = []
        for _si, _sk, _sn, beats in cur:
            n_want = _n_shots_for(beats)
            packed = _pack_beats(beats, n_shots=n_want)
            natural.append(_content_duration_sec(packed))
        return _renumber_plan(cur), natural

    plan, floor_durs = _ensure_episode_runtime(plan)
    plan = plan[:max_segments]
    floor_durs = floor_durs[: len(plan)]

    # 相邻段台词完全相同则去重（防止同一句出现在段1又段2）
    deduped: List[Tuple[int, int, str, List[Dict[str, str]]]] = []
    prev_dlg_key = ""
    for item in plan:
        dlg_key = "｜".join(
            f"{(b.get('role') or '').strip()}：{(b.get('text') or '').strip()}"
            for b in item[3]
            if b.get("type") == "dialogue" and (b.get("text") or "").strip()
        )
        if dlg_key and dlg_key == prev_dlg_key:
            continue
        deduped.append(item)
        if dlg_key:
            prev_dlg_key = dlg_key
    if deduped:
        plan = _renumber_plan(deduped)
        floor_durs = floor_durs[: len(plan)]

    # 同场角色并集 → 全场共用站位轴；段间写入位置承接
    scene_role_map: Dict[int, List[str]] = {}
    scene_master_map: Dict[int, str] = {}
    for scene_idx, _seg_k, scene_name, beats in plan:
        bucket = scene_role_map.setdefault(scene_idx, [])
        for r in _roles_in_beats(beats):
            if r not in bucket:
                bucket.append(r)
        if scene_idx not in scene_master_map:
            light = _scene_lighting_clause(scene_name, title)
            scene_master_map[scene_idx] = (
                f"场景母版「{scene_name}」：同一场地、同一时段光影色温、同一陈设门窗朝向；"
                f"{light + '；' if light else ''}"
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
            floor_dur=floor_durs[p_num - 1] if p_num - 1 < len(floor_durs) else 0,
        )
        segs.append(body)
        prev_scene_idx = scene_idx

    return "\n\n".join(segs)



def _extract_p_segment_dialogues(text: str) -> List[Tuple[str, str, str]]:
    """返回 [(P编号, 标题行, 台词文本), ...]。"""
    out: List[Tuple[str, str, str]] = []
    for part in re.split(r"(?=^P\d+[｜|])", text or "", flags=re.M):
        part = part.strip()
        hm = re.match(r"^(P(\d+)[｜|].+?)(?:\n|$)", part)
        if not hm:
            continue
        header = hm.group(1).strip()
        pnum = hm.group(2)
        lines: List[str] = []
        for m in re.finditer(r"【台词】\s*([^\n]*)", part):
            t = (m.group(1) or "").strip()
            if t and t not in ("无。", "无", "无台词", "（无）"):
                lines.append(t)
        out.append((pnum, header, "｜".join(lines)))
    return out


def _video_script_quality_ok(text: str, draft: str = "") -> bool:
    """拒绝「同号重复 / 连续多段同一句台词」等明显坏稿。"""
    segs = _extract_p_segment_dialogues(text)
    if len(segs) < 3:
        return False
    nums = [s[0] for s in segs]
    if len(nums) != len(set(nums)):
        return False
    # P 编号应基本递增
    try:
        ints = [int(n) for n in nums]
        if ints != sorted(ints):
            return False
    except ValueError:
        return False
    # 连续 ≥3 段台词完全相同（且非空）→ 坏稿
    same_run = 1
    for i in range(1, len(segs)):
        if segs[i][2] and segs[i][2] == segs[i - 1][2]:
            same_run += 1
            if same_run >= 3:
                return False
        else:
            same_run = 1
    # 残句：引号内不足 6 字或停在「名字，」→ 坏稿
    for _n, _h, dlg in segs:
        m = re.search(r"[「\"“](.+?)[」\"”]", dlg or "")
        inner = (m.group(1) if m else dlg or "").strip()
        if not inner:
            continue
        if _dialogue_char_count(inner) < 6 and inner.endswith(("，", ",", "、")):
            return False
    # 与草稿比：草稿有 ≥4 段不同台词时，成稿有效台词种类过少也判失败
    if draft:
        draft_dlg = {d for _, _, d in _extract_p_segment_dialogues(draft) if d}
        out_dlg = {d for _, _, d in segs if d}
        if len(draft_dlg) >= 4 and len(out_dlg) < max(3, len(draft_dlg) // 2):
            return False
    return True


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
                "按「场→分段」输出：每段 P00x｜标题·场景名｜场N段M｜总时长：N秒（3–10秒，按时长贴内容）；"
                "单段仅允许1人开口，多人对话必须拆成多段；"
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
                f"场景资产：{', '.join(s.get('name','') for s in scenes[:15])}\n"
                f"道具：{', '.join(p.get('name','') for p in props[:15])}\n\n"
                "硬性规则：每一 P 段总时长 3–10 秒（不得超过 10 秒）；"
                "整集所有 P 段总时长合计不得低于 60 秒（建议 60–120 秒，约 7–14 段）；"
                "时长按内容：对白/动作充实的完整段写约 8–9 秒（说完用微表情/走动承接至镜尾），"
                "禁止把完整内容硬拆成一串 3–5 秒碎段；"
                "仅极短句、轻动作、易空声的段才写 3–5 秒，须用有效表演填满；"
                "内容少凑不够 60 秒时，可另加反应/承接短段，不要拆碎已完整的对白段；"
                "按台词量拆段：单段仅 1 人一句对白，建议约 14–28 个汉字可落在 8–9 秒；"
                "多人对话必须拆成独立 P 段，禁止一段堆多句对白；"
                "每段脚本自洽（含风格/锁定/本段镜头）。\n"
                "【台词与编号·严禁出错】\n"
                "1) P001、P002、P003…必须严格递增，禁止多个段落共用同一个 P 编号。\n"
                "2) 每一段的【台词】必须来自草稿/分集剧本中对应那一句，禁止把第一句台词复制到后续每一段。\n"
                "3) 草稿里有几句不同对白，成稿就必须保留这些不同对白（可润色表演，不可串句/复读）。\n"
                "【场次拆分·必须遵守】\n"
                "1) 剧本里每个场标必须对应独立「场」编号（场1、场2、场3…），支持："
                "「1-1 赛车场 夜 外」「场景1-2 外景·…」「EP…-SC…」；"
                "标题里的场景名用真实地点（如「外景·赛车场·夜」），严禁「本集场景」或全挤进场1。\n"
                "2) 「梗概：…」「人：…」「人物：…」不是对白、也不是一场戏；开场必须从第一个真场标"
                "（如 1-1 赛车场）开始，禁止先念梗概。\n"
                "3) 换景即换场：赛车场 / 路边 / 酒店房间等不同地点必须不同场号；同一场内再拆段1、段2…\n"
                "4) 草稿参考若已按多场拆好，请保持场号与场景名，仅润色镜头与衔接；剧情与台词必须贴合分集剧本原文。\n"
                "【衔接与朝向·必须遵守】\n"
                "1) 同一「场」下的连续 P 段必须共用同一[场景母版]与[站位轴]：同一房间/场地、同一光色、同一左右站位，禁止每段换景或人物左右对调。\n"
                "2) 对白时说话人必须朝向听话人（眼线看对方脸/肩），写明「甲朝乙」；严禁「面对镜头说话/正对镜头念白」。\n"
                "3) 每段首镜【位置承接】必须承接上一段末状态（人物站哪、脸朝哪、背景陈设）；只推进表演，不重摆。\n"
                "4) 【画面】写清人物左右位置与朝向，不要只写情绪形容词。\n"
                "5) 对白尽快开嗓（约 0.3–1 秒起说），说完立刻停口；"
                "充实段说完后用微表情/走动/环境声接到 8–9 秒镜尾，禁止静默发呆或慢推空镜。\n"
                "【光线与表演·必须遵守】\n"
                "1) [全局光影氛围]与【画面】必须写明主光方向、色温与明暗层次"
                "（按场景日/夜/黄昏/阴雨推断：侧光/逆光/轮廓光/窗光/路灯等），"
                "面部高光可读；禁止平光、死黑糊脸、过曝死白。\n"
                "2) 人物禁止木桩站桩、表情定格、双手僵垂、拍照感；"
                "须写微表情、眨眼、呼吸、重心微调、手部小动作或走动/转身。\n"
                "3) 运镜要节奏利落，禁止慢动作拖沓；全程须有环境声或对白，禁止静默空白。\n"
                "4) 无对白镜也要用干脆动作+环境声填满短时长，禁止空镜发呆硬撑秒数。\n\n"
                f"分集剧本：\n{(ep.get('script_md') or '')[:10000]}\n\n"
                f"草稿参考：\n{draft[:8000]}"
            ),
        },
    ]
    try:
        text = chat_completion(
            messages, model=script_model(model), temperature=0.45, max_tokens=14000
        ).strip()
    except Exception:
        return draft
    if not _video_script_quality_ok(text, draft):
        # LLM 常见故障：把 P001 台词复制到整场 → 回退结构化草稿保台词正确
        return draft
    return text


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
    from .script_normalize import normalize_imported_script

    text = (novel_text or "").strip()
    if not text:
        raise ValueError("剧本内容为空")
    if len(text) > 600_000:
        raise ValueError("剧本过长（建议不超过约 20 万字）")
    # 导入即自动排版为系统标准（场景N-M / 人物： / △ / 对白）
    text = normalize_imported_script(text)
    eps = split_novel_episodes(text)
    if not eps:
        raise ValueError(
            "未能识别分集。支持：第001集｜标题、第1集：标题、第一集、EP01《标题》、第1章 标题"
        )
    # 标题：优先取《书名》
    title_m = re.search(r"《([^》]+)》", (novel_text or "")[:200])
    proj_name = (name or "").strip() or (
        f"《{title_m.group(1)}》" if title_m else f"漫剧_{eps[0].get('title') or '新项目'}"
    )
    width, height = _aspect_to_wh(aspect_ratio)
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
            "script_normalized": True,
        }
    )
    sync_project_episodes(pid, eps)
    services.update_project_docs(
        pid,
        outline_md=_format_outline_md(summary, eps),
        series_scripts_note="小说/剧本导入（已自动排版为系统标准格式）",
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
    art_style: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    from .script_normalize import normalize_imported_script

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
    # 解析前再次自动排版，兼容旧项目/手工粘贴的非标格式
    novel = normalize_imported_script(novel)
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
    # 本剧画风优先：请求体 > 项目 style_prefix > meta，不再被全局默认覆盖
    art = (
        (art_style or "").strip()
        or (proj.get("style_prefix") or "").strip()
        or (meta.get("art_style") or "").strip()
        or "半写实3D国漫"
    )
    if use_llm:
        try:
            summary = llm_script_summary(novel, episodes, model=model, art_style=art)
        except Exception as e:
            summary = heuristic_script_summary(novel, episodes)
            summary["llm_error"] = str(e)[:200]
    else:
        summary = heuristic_script_summary(novel, episodes)
    summary["art_style"] = art
    summary["aspect_ratio"] = (
        (aspect_ratio or "").strip()
        or meta.get("aspect_ratio")
        or summary.get("aspect_ratio")
        or "16:9"
    )
    summary["mode"] = (mode or "").strip() or meta.get("mode") or "默认版"
    summary["parse_status"] = "done"
    summary["workflow_step"] = "outline"
    summary["episode_count"] = len(episodes)
    summary["split_mode"] = (
        "ep"
        if EP_HEAD_RE.search(clean_novel_text(novel))
        else "chapter"
        if len(episodes) > 1
        else "single"
    )
    # 扩容缺集并写入正文；同步画风与画幅尺寸（9:16 / 16:9）
    sync_project_episodes(project_id, episodes)
    w, h = _aspect_to_wh(summary["aspect_ratio"])
    services.update_project(
        project_id, style_prefix=art, width=w, height=h
    )
    summary["script_normalized"] = True
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
        "normalized": True,
    }


def merge_character_age_aliases(
    project_id: str,
    *,
    generate_missing_images: bool = True,
) -> Dict[str, Any]:
    """把「十一岁的白晓月」「白媛媛」等并入主角色，多年龄用多图槽放置。"""
    from . import db as _db

    art = (
        (services.get_project(project_id).get("style_prefix") or "").strip()
        or "半写实3D国漫"
    )
    chars = services.list_characters(project_id)
    by_name = {c.get("name"): c for c in chars if c.get("name")}
    merged: List[Dict[str, str]] = []
    deleted: List[str] = []
    generated: List[str] = []

    # 1) 别名/年龄名 → 并入主角色
    for ch in list(chars):
        raw = (ch.get("name") or "").strip()
        canon, state = _resolve_character_identity(raw)
        if not canon:
            continue
        # 已是主名且无状态：跳过
        if canon == raw and not state:
            continue
        # 主名不存在则把当前条目改名为主名
        main = by_name.get(canon)
        if main is None:
            services.upsert_character(
                project_id,
                {
                    "id": ch.get("id"),
                    "name": canon,
                    "role_note": (ch.get("role_note") or "").replace(raw, canon),
                    "visual_prompt": ch.get("visual_prompt") or "",
                    "voice_desc": ch.get("voice_desc") or "",
                    "episodes": ch.get("episodes") or [],
                    "ref_image": ch.get("ref_image") or "",
                    "ref_images": ch.get("ref_images") or [],
                    "voice_samples": ch.get("voice_samples") or [],
                    "tts_voice": ch.get("tts_voice") or "",
                    "sort_order": int(ch.get("sort_order") or 0),
                },
            )
            by_name = {c.get("name"): c for c in services.list_characters(project_id)}
            merged.append({"from": raw, "to": canon, "state": state or "基础"})
            continue
        if main.get("id") == ch.get("id"):
            continue

        # 合并 episodes
        eps: List[int] = []
        for x in list(main.get("episodes") or []) + list(ch.get("episodes") or []):
            try:
                xi = int(x)
            except Exception:
                continue
            if xi not in eps:
                eps.append(xi)

        # 合并图片：别名图 → 主角色状态槽（无状态的脏名只并图，不新建状态）
        images = list(main.get("ref_images") or [])
        label = (state or "").strip()
        existing_labels = {
            (im.get("label") if isinstance(im, dict) else "") or "" for im in images
        }
        src_imgs = list(ch.get("ref_images") or [])
        if not src_imgs and ch.get("ref_image"):
            src_imgs = [{"path": ch.get("ref_image"), "label": label or "形象"}]
        for im in src_imgs:
            if isinstance(im, str):
                item = {"path": im, "label": label or "形象"}
            else:
                item = dict(im)
                if label:
                    item["label"] = label
            if label and not item.get("desc"):
                item["desc"] = _child_state_appearance(
                    canon,
                    label,
                    art,
                    base_visual=main.get("visual_prompt") or "",
                )
            paths = {
                (x.get("path") if isinstance(x, dict) else x) for x in images
            }
            if item.get("path") and item["path"] not in paths:
                lab = item.get("label") or "形象"
                n = 2
                while lab in existing_labels:
                    lab = f"{(label or '形象')}-{n}"
                    n += 1
                item["label"] = lab
                existing_labels.add(lab)
                images.append(item)

        ref0 = ""
        for im in images:
            p = im.get("path") if isinstance(im, dict) else im
            if p:
                ref0 = p
                break
        services.upsert_character(
            project_id,
            {
                "id": main["id"],
                "name": canon,
                "role_note": main.get("role_note") or ch.get("role_note") or "",
                "visual_prompt": main.get("visual_prompt") or ch.get("visual_prompt") or "",
                "voice_desc": main.get("voice_desc") or ch.get("voice_desc") or "",
                "episodes": eps,
                "ref_image": ref0 or main.get("ref_image") or "",
                "ref_images": images,
                "voice_samples": main.get("voice_samples") or [],
                "tts_voice": main.get("tts_voice") or "",
                "sort_order": int(main.get("sort_order") or 0),
            },
        )
        with _db.connect() as conn:
            conn.execute(
                "DELETE FROM characters WHERE project_id=? AND id=?",
                (project_id, ch["id"]),
            )
        deleted.append(raw)
        merged.append({"from": raw, "to": canon, "state": label})
        by_name = {c.get("name"): c for c in services.list_characters(project_id)}

    # 2) 从剧本收集年龄/别名状态，补出缺失多图（库表不存空 path，直接按状态生成）
    ep_rows = services.list_episodes(project_id)
    extracted = extract_assets_from_scripts(
        [
            {
                "ep_num": e.get("ep_num"),
                "title": e.get("title"),
                "script_md": e.get("script_md") or "",
            }
            for e in ep_rows
        ]
    )
    needed_states: Dict[str, List[str]] = {}
    for c in extracted.get("characters") or []:
        canon = (c.get("name") or "").strip()
        if not canon:
            continue
        for st in c.get("_states") or []:
            if not st:
                continue
            needed_states.setdefault(canon, [])
            if st not in needed_states[canon]:
                needed_states[canon].append(st)
    # 合并过程中登记过的状态
    for m in merged:
        canon = (m.get("to") or "").strip()
        st = (m.get("state") or "").strip()
        if canon and st and "人物：" not in st:
            needed_states.setdefault(canon, [])
            if st not in needed_states[canon]:
                needed_states[canon].append(st)

    # 3) 缺哪个状态图就补生成
    if generate_missing_images and services.storyboard_image_api_ready():
        by_name = {c.get("name"): c for c in services.list_characters(project_id)}
        for canon, states in needed_states.items():
            ch = by_name.get(canon)
            if not ch:
                continue
            have = set()
            for im in ch.get("ref_images") or []:
                if not isinstance(im, dict):
                    continue
                lab = (im.get("label") or "").strip()
                if lab:
                    have.add(lab)
                    # AI·11岁形态 也算有
                    if lab.startswith("AI·"):
                        have.add(lab[3:])
            for st in states:
                # 兼容旧标签「11岁形态」→ 统一槽位名
                slot = (
                    "回忆杀·青涩态"
                    if re.search(r"\d{1,2}岁", st)
                    or any(k in st for k in ("幼年", "童年", "少年", "少女", "青涩"))
                    else st
                )
                if slot in have or f"AI·{slot}" in have:
                    continue
                desc = _child_state_appearance(
                    canon,
                    slot,
                    art,
                    base_visual=ch.get("visual_prompt") or "",
                )
                try:
                    services.generate_character_image_via_ohmygpt(
                        project_id,
                        ch["id"],
                        angle_label=slot,
                        appearance_override=desc,
                        sheet_mode="body",
                        lock_face_to_base=True,
                    )
                    generated.append(f"{canon}:{slot}")
                    have.add(slot)
                except Exception as e:
                    generated.append(f"{canon}:{slot}:FAIL:{str(e)[:80]}")
            by_name = {c.get("name"): c for c in services.list_characters(project_id)}

    return {
        "merged": merged,
        "deleted": deleted,
        "generated": generated,
        "characters": services.list_characters(project_id),
    }


def parse_project_assets(
    project_id: str,
    *,
    use_llm: bool = True,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """重新解析角色/场景/道具。

    只重建资产库（并尽量按名称保留已有设定图/音色）；
    不修改、不清空各集 storyboard_md（视频脚本与台词）。
    """
    proj = services.get_project(project_id)
    docs = proj.get("docs") or {}
    novel = clean_novel_text((docs.get("novel_md") or "").strip())
    eps = services.list_episodes(project_id)
    # 若仍是整本塞进 1 集，先按章切分扩容，避免分集脚本空白
    # sync 只写 script_md，不会动 storyboard_md
    if novel:
        split_eps = split_novel_episodes(novel)
        if len(split_eps) > max(1, len(eps)):
            sync_project_episodes(project_id, split_eps)
            eps = services.list_episodes(project_id)
            services.update_project_docs(project_id, novel_md=novel)
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
    # 资产解析使用本剧 style_prefix / art_style
    art = (
        (proj.get("style_prefix") or "").strip()
        or (meta.get("art_style") or "").strip()
        or "半写实3D国漫"
    )
    llm_note = ""
    chars_h = list(assets.get("characters") or [])
    n_chars_h = len(chars_h)
    n_scenes_h = len(assets.get("scenes") or [])
    has_cast_list = any(c.get("_from_people") for c in chars_h)
    # 无人物表且（角色过少 / 噪声过多）：改走 LLM 名单，勿用伪角色去润色
    weak_hits = sum(
        1
        for c in chars_h
        if int(c.get("_hits") or 0) < 3 and not (c.get("_lines") or [])
    )
    need_llm_chars = (not has_cast_list) and (
        n_chars_h < 2 or weak_hits >= max(2, n_chars_h // 2) or n_chars_h > 12
    )
    # 网文无 EP-SC 场标时场景常为空，必须 LLM 补场景/道具
    need_llm_scenes = n_scenes_h == 0
    if use_llm and (need_llm_chars or need_llm_scenes):
        try:
            llm_pack = llm_extract_assets_from_novel(
                novel, ep_payload, art_style=art, model=model
            )
            base_for_merge = {
                "characters": [] if need_llm_chars else (assets.get("characters") or []),
                "scenes": [] if need_llm_scenes else (assets.get("scenes") or []),
                "props": assets.get("props") or [],
            }
            merged = _merge_asset_packs(base_for_merge, llm_pack)
            if need_llm_chars:
                assets["characters"] = merged.get("characters") or assets.get("characters") or []
            if need_llm_scenes:
                assets["scenes"] = merged.get("scenes") or assets.get("scenes") or []
            assets["props"] = merged.get("props") or assets.get("props") or []
            meta["asset_extract"] = "llm"
        except Exception as e:
            llm_note = f"llm_extract:{str(e)[:180]}"
            meta["asset_extract_error"] = llm_note
            meta["asset_extract"] = "heuristic"
    else:
        meta["asset_extract"] = "heuristic"
    # 再清一次伪名 + 限制数量，防止润色拖死
    cleaned_chars: List[Dict[str, Any]] = []
    for c in assets.get("characters") or []:
        nm = _clean_char_name(c.get("name") or "")
        if not nm:
            continue
        c = dict(c)
        c["name"] = nm
        cleaned_chars.append(c)
    cleaned_chars.sort(
        key=lambda c: (
            int(c.get("_hits") or 0),
            len(c.get("episodes") or []),
            len(c.get("_lines") or []),
        ),
        reverse=True,
    )
    assets["characters"] = cleaned_chars[:12]
    assets["scenes"] = list(assets.get("scenes") or [])[:12]
    assets["props"] = list(assets.get("props") or [])[:10]
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

    # 先用剧本上下文补全描述并落盘；解析接口不再阻塞跑 LLM 润色（易超时转圈）
    # 需要润色时走 /studio/enrich-descriptions
    assets = fill_asset_descriptions(assets, art_style=art)
    counts = _persist(assets)
    meta["assets_status"] = "done"
    meta["workflow_step"] = "assets"
    meta["asset_counts"] = counts
    meta["asset_enrich"] = "heuristic"
    if llm_note:
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
    art = (
        (proj.get("style_prefix") or "").strip()
        or (meta.get("art_style") or "").strip()
        or "半写实3D国漫"
    )
    id_set = {str(x).strip() for x in (asset_ids or []) if str(x).strip()}

    chars = services.list_characters(project_id)
    scenes = services.list_scenes(project_id)
    props = services.list_props(project_id)
    if id_set:
        chars = [c for c in chars if c.get("id") in id_set]
        scenes = [s for s in scenes if s.get("id") in id_set]
        props = [p for p in props if p.get("id") in id_set]

    # 合并抽取上下文到现有资产；弱描述/撞脸模板清空后由 fill 重写
    force_char_looks = bool(meta.get("force_character_looks"))
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
        if force_char_looks or _is_weak_text(item.get("role_note") or "", name, min_len=18):
            item["role_note"] = ""
        if force_char_looks or _is_weak_text(item.get("visual_prompt") or "", name, min_len=20):
            item["visual_prompt"] = ""
        if force_char_looks or _is_weak_text(item.get("voice_desc") or "", name, min_len=16):
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
    # 角色字段已按需清空；勿 force 全局，避免冲掉场景/道具好描述
    pack = fill_asset_descriptions(pack, art_style=art, force=False)
    if use_llm:
        try:
            pack = llm_enrich_assets(novel, pack, art_style=art, model=model)
        except Exception as e:
            meta["asset_llm_error"] = str(e)[:200]
    if force_char_looks:
        meta.pop("force_character_looks", None)

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
    # 短剧场标（1-1 赛车场 夜 外）一律走结构化拆段，保证对白/△描写完整，不交给 LLM 改写
    script_body = ep.get("script_md") or ""
    has_short_scenes = bool(SHORT_SCENE_RE.search(script_body))
    draft = build_video_script_text(
        ep=ep,
        summary=meta,
        characters=chars,
        scenes=scenes,
        props=props,
        segment_sec=9,
    )
    text = draft
    used_llm = False
    if use_llm and not has_short_scenes:
        text = llm_video_script(
            ep=ep,
            summary=meta,
            characters=chars,
            scenes=scenes,
            props=props,
            model=model,
        )
        used_llm = True
    # 最终闸门：坏稿绝不落库（LLM 失败或质量不达标 → 回退草稿）
    if not _video_script_quality_ok(text, draft):
        text = draft
        used_llm = False
    if not _video_script_quality_ok(text, draft):
        raise ValueError("视频脚本生成失败：台词结构不合格，请检查分集正文场标与对白")
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
        "used_llm": used_llm,
        "from_draft": not used_llm,
    }


def _field_blocks(part: str, label: str) -> List[str]:
    """提取标签后内容。

    兼容：
    - 【台词】… / 【画面】…
    - 台词｜… / 画面描述｜… / 台词：…
    - [台词] …
    """
    text = part or ""
    stop = (
        r"(?:\n【|\n\[|"
        r"\n(?:画面描述|画面|台词|音效|运镜|景别|镜号|时长|连续性要求|位置承接|OS|画外音)\s*[｜|:：]|"
        r"\n镜头\s*\d+|P\d+｜|\Z)"
    )
    pats = [
        rf"【{re.escape(label)}】\s*(?:\n)?(.*?){stop}",
        rf"\[{re.escape(label)}\]\s*(?:\n)?(.*?){stop}",
        rf"(?:^|\n){re.escape(label)}\s*[｜|:：]\s*(.*?){stop}",
    ]
    out: List[str] = []
    for pat in pats:
        for m in re.findall(pat, text, flags=re.S):
            s = (m or "").strip()
            if s:
                out.append(s)
    return out


def _parse_dialogue_line(line: str) -> Optional[Dict[str, Any]]:
    line = (line or "").strip()
    if not line or line in ("无。", "无", "无对白", "（无）"):
        return None
    # 去掉行首「画外音/OS」前缀
    line = re.sub(r"^(?:画外音|OS|旁白)\s*[:：]\s*", "", line).strip()
    line = re.sub(r"^(?:画外音|OS|旁白)\s+", "", line).strip()
    # 标准：角色：台词 / 角色（情绪）：「台词」
    m = re.match(
        r"^([\u4e00-\u9fffA-Za-z0-9·]{1,16})"
        r"(?:（([^）]*)）|\(([^)]*)\))?"
        r"[：:]\s*"
        r"[「\"“]?(.*?)[」\"”]?\s*$",
        line,
    )
    if m:
        text = (m.group(4) or "").strip().strip("「」\"“”")
        if text and text not in ("无", "无。"):
            return {
                "role": m.group(1),
                "text": text,
                "emotion": (m.group(2) or m.group(3) or "").strip(),
                "start_sec": 0.0,
            }
    # 导演本常见：秀兰，成熟温和…：“孩子？醒醒……”团团不说话。
    m2 = re.search(
        r"^([\u4e00-\u9fffA-Za-z0-9·]{1,16})"
        r"[，,：:].*?"
        r"[「\"“]([^」\"”]{1,80})[」\"”]",
        line,
        flags=re.S,
    )
    if m2:
        text = (m2.group(2) or "").strip()
        if text and text not in ("无", "无。"):
            emo = ""
            em = re.search(r"(轻声|担忧|急切|愤怒|冷笑|哭腔|低声)[^「\"“]{0,12}", line)
            if em:
                emo = em.group(1)
            return {
                "role": m2.group(1),
                "text": text,
                "emotion": emo,
                "start_sec": 0.0,
            }
    return None


def _lens_start_sec(block: str) -> Optional[float]:
    """从「镜头N（3—12秒）」或「镜头N（08:00—15:00）」取起始秒。

    注意：本流水线生成的「08:00」表示第 8 秒（假冒 MM:SS 写法），不是 8 分 0 秒。
    """
    m = re.search(
        r"镜头\s*\d+\s*[（(]\s*(\d+(?:\.\d+)?)\s*[—\-~～到至]\s*(\d+(?:\.\d+)?)\s*秒",
        block or "",
    )
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m2 = re.search(
        r"镜头\s*\d+\s*[（(]\s*(\d{1,2}):(\d{2})\s*[—\-~～]\s*(\d{1,2}):(\d{2})",
        block or "",
    )
    if m2:
        try:
            a, b = int(m2.group(1)), int(m2.group(2))
            c, d = int(m2.group(3)), int(m2.group(4))
            # 段内时间码：08:00—15:00 = 第8秒到第15秒（秒字段多为 00）
            if b == 0 and d == 0 and a <= 30 and c <= 30:
                return float(a)
            return float(a * 60 + b)
        except ValueError:
            return None
    return None


def _dedupe_visual_clauses(text: str, max_len: int = 720) -> str:
    """去掉重复拼接的场景母版/站位轴/避免条款。"""
    parts = [p.strip(" 。；;") for p in re.split(r"[；;]", text or "") if p and p.strip(" 。；;")]
    out: List[str] = []
    seen: set = set()
    for p in parts:
        key = re.sub(r"\s+", "", p)
        if len(key) < 4:
            continue
        if key in seen:
            continue
        # 近重复：后段完全包含前段关键句则跳过更短的
        if any(key in s or s in key for s in seen if min(len(key), len(s)) > 24):
            # 保留更长的那条
            longer = True
            for i, prev in enumerate(out):
                pk = re.sub(r"\s+", "", prev)
                if key in pk or pk in key:
                    if len(key) > len(pk):
                        out[i] = p
                        seen.discard(pk)
                        seen.add(key)
                    longer = False
                    break
            if not longer:
                continue
        seen.add(key)
        out.append(p)
    return "；".join(out)[:max_len]


def _scrub_facing_contradictions(visual: str) -> str:
    """去掉过时的「禁止正脸说话」硬限制，保留可正脸/可边走边说。"""
    v = visual or ""
    v = v.replace("禁止正对镜头念白", "可正脸对镜头说话")
    v = v.replace("禁止正对镜头", "可正脸对镜头")
    v = v.replace("禁止面对镜头说话", "可正脸对镜头说话")
    v = v.replace("眼线不看镜头", "眼线可看对象或镜头")
    v = v.replace("朝向对方而非镜头", "可看向对方或正脸对镜头")
    return v


def _lens_duration_sec(block: str, default: float = 5.0) -> float:
    """从「镜头N（08:00—15:00，7秒）」取本镜时长。"""
    m = re.search(r"[，,]\s*(\d+(?:\.\d+)?)\s*秒\s*[）)]", block or "")
    if m:
        try:
            return max(2.0, min(15.0, float(m.group(1))))
        except ValueError:
            pass
    m2 = re.search(
        r"镜头\s*\d+\s*[（(]\s*(\d{1,2}):(\d{2})\s*[—\-~～]\s*(\d{1,2}):(\d{2})",
        block or "",
    )
    if m2:
        try:
            a, b = int(m2.group(1)), int(m2.group(2))
            c, d = int(m2.group(3)), int(m2.group(4))
            if b == 0 and d == 0 and a <= 30 and c <= 30:
                return max(2.0, min(15.0, float(c - a)))
            return max(2.0, min(15.0, float((c * 60 + d) - (a * 60 + b))))
        except ValueError:
            pass
    return float(default)


def _dialogue_clip_duration(voices: List[Dict[str, Any]], lens_dur: float) -> float:
    """纯对白镜时长：刚好够说完 + 很短收尾（无前半静场时用）。"""
    from .drama10_export import _estimate_speak_sec

    need = 0.45
    for v in voices or []:
        need += _estimate_speak_sec(v.get("text") or "") + 0.25
    need += 0.9
    _ = lens_dur
    return max(4.0, min(8.0, need))


def _parse_lens_block(lp: str) -> Dict[str, Any]:
    """解析单个「镜头N」块 → voices / visual / duration。"""
    lens_voices: List[Dict[str, Any]] = []
    for label in ("台词", "画外音", "OS"):
        for block in _field_blocks(lp, label):
            # 整块先试（导演本常把角色+引号写在同一段）
            parsed = _parse_dialogue_line(block)
            if parsed:
                if label in ("画外音", "OS") and not parsed.get("role"):
                    parsed["role"] = "旁白"
                if label in ("画外音", "OS"):
                    parsed["emotion"] = (parsed.get("emotion") or "画外音").strip()
                lens_voices.append(parsed)
                continue
            for line in block.splitlines() or [block]:
                parsed = _parse_dialogue_line(line)
                if not parsed:
                    continue
                if label in ("画外音", "OS") and not parsed.get("role"):
                    parsed["role"] = "旁白"
                if label in ("画外音", "OS"):
                    parsed["emotion"] = (parsed.get("emotion") or "画外音").strip()
                lens_voices.append(parsed)
                break
    uniq: List[Dict[str, Any]] = []
    seen = set()
    for v in lens_voices:
        key = (v.get("role"), v.get("text"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(v)
    visuals = [
        b
        for lab in ("画面描述", "画面")
        for b in _field_blocks(lp, lab)
        if b and b != "无。"
    ]
    vis_raw = visuals[0] if visuals else ""
    vis_raw = re.sub(r"同场站位锁定[：:].*$", "", vis_raw).strip("；; ")
    vis_raw = re.sub(r"场景与站位[：:].*$", "", vis_raw).strip("；; ")
    vis_raw = re.sub(r"保持同场站位与陈设[：:].*$", "", vis_raw).strip("；; ")
    # 导演本时长｜7秒
    dur = _lens_duration_sec(lp, default=5.0)
    dm = re.search(r"(?:^|\n)时长\s*[｜|:：]\s*(\d+(?:\.\d+)?)\s*秒", lp or "")
    if dm:
        try:
            dur = max(2.0, min(15.0, float(dm.group(1))))
        except ValueError:
            pass
    return {
        "voices": uniq[:6],
        "visual": vis_raw,
        "duration": dur,
    }


def _scrub_silent_visual(visual: str) -> str:
    visual = re.sub(
        r"(开嗓后?|说话|对白|口型与台词同步|口型|念白|SPEAK\s*ONLY)",
        "",
        visual,
        flags=re.I,
    )
    visual = re.sub(r"尚未开口", "静场", visual)
    visual = re.sub(r"[；;]{2,}", "；", visual).strip("；;，, ")
    if "全程闭口" not in visual:
        visual = (visual + "；全程闭口，仅环境声。").strip("；")
    return visual


def _scene_lighting_clause(scene_name: str = "", title: str = "", part_text: str = "") -> str:
    """按日/夜/黄昏等推断场景光线，避免平光死黑。"""
    # 场景名优先（如「外·广场·日」）；part_text 仅作补充，勿把「色温」等泛词当已写光线
    blob = f"{scene_name} {title}"
    hint = (part_text or "")[:200]
    if re.search(r"(主光|侧光|逆光|轮廓光|日景光线|夜景光线|黄昏光线|晨光：)", f"{blob} {hint}"):
        return ""
    if re.search(r"夜|晚|午夜|灯火|霓虹|月色|月光|深夜", blob) or re.search(
        r"夜|晚|午夜|灯火|霓虹|月色|月光|深夜", hint
    ):
        return (
            "夜景光线：暖色主光（室内灯/路灯）侧打面部，冷蓝环境补光，"
            "轮廓光勾边，眼窝鼻梁高光可读，阴影有层次，禁止全黑糊脸或死白平光。"
        )
    if re.search(r"黄昏|傍晚|夕阳|暮色|落日", blob) or re.search(
        r"黄昏|傍晚|夕阳|暮色|落日", hint
    ):
        return (
            "黄昏光线：低角度暖橙侧逆光勾轮廓，天空冷暖渐变，"
            "面部有柔和补光，禁止纯黑剪影或灰蒙一片。"
        )
    if re.search(r"阴天|阴雨|雨夜|细雨", blob) or re.search(r"阴天|阴雨|雨夜|细雨", hint):
        return (
            "阴雨光线：柔散射天光，路面湿反光，冷灰环境，"
            "人物脸部仍清晰柔亮，避免闷灰死黑。"
        )
    if re.search(r"清晨|黎明|晨光|拂晓", blob) or re.search(r"清晨|黎明|晨光|拂晓", hint):
        return "晨光：低角度清冷侧光，柔和高光，空气感轻微，面部通透。"
    # 剧本常见「·日」「日景」「白天」「午后」
    if re.search(r"(?:^|[·・\s｜|])日(?:$|[·・\s｜|])|日景|白天|日间|午后|上午|户外|外景", blob) or re.search(
        r"(?:^|[·・\s｜|])日(?:$|[·・\s｜|])|日景|白天|日间|午后|上午", hint
    ):
        return (
            "日景光线：明确主光方向（阳光/窗光侧光），柔和投影，"
            "暗部有环境反射补亮，肤色通透有体积感，禁止平光死白或死黑。"
        )
    return (
        "光影：明确主光方向与色温，面部高光可读，环境明暗层次与体积感，"
        "禁止平光、死黑糊脸或过曝死白。"
    )


def _alive_performance_clause(*, has_dialogue: bool) -> str:
    """避免人物木桩站桩、表情僵硬。"""
    if has_dialogue:
        return (
            "人物表演自然生动：微表情、眨眼、呼吸起伏、重心微调、手部小动作与衣袂随动；"
            "可缓慢走动、转身或边说边势，禁止木桩站桩、双手僵垂、表情定格、拍照感。"
        )
    return (
        "人物持续轻动作：视线游移、呼吸、整理衣袖/重心转移/缓慢走动，"
        "禁止全程僵立定格或木偶感。"
    )


def _enrich_visual_lighting_motion(
    visual: str,
    *,
    scene_name: str = "",
    title: str = "",
    part_text: str = "",
    has_dialogue: bool = False,
) -> str:
    """给画面描述补光线与动态表演（已有则不重复）。"""
    v = (visual or "").strip()
    light = _scene_lighting_clause(scene_name, title, part_text or v)
    alive = _alive_performance_clause(has_dialogue=has_dialogue)
    bits = [v] if v else []
    if light and not re.search(r"(主光|侧光|逆光|轮廓光|日景光线|夜景光线)", v):
        bits.append(light)
    if not re.search(r"(微表情|走动|呼吸|禁止木桩|禁止.*僵|轻动作)", v):
        bits.append(alive)
    return _dedupe_visual_clauses("；".join(x for x in bits if x), max_len=360)


def _video_script_to_shots(text: str) -> List[Dict[str, Any]]:
    """每个 P 段 → 一条成片（一次 Comfy/H3 生成，禁止分段混音拼接）。

    段内多「镜头」只作时间轴：累计秒数写入 start_sec；模型原生音轨一次出声。
    """
    from .drama10_export import _estimate_speak_sec

    shots: List[Dict[str, Any]] = []
    parts = re.split(r"(?=^P\d+[｜|])", text or "", flags=re.M)
    for part in parts:
        part = part.strip()
        if not re.match(r"^P\d+[｜|]", part):
            continue
        hm = re.match(r"^(P\d+[｜|].+?)(?:\n|$)", part)
        title_line = hm.group(1).strip() if hm else part.splitlines()[0][:80]
        title_short = re.sub(r"｜总时长：\d+秒", "", title_line)
        dm = re.search(r"总时长[：:]\s*(\d+)\s*秒", title_line)
        seg_dur = float(dm.group(1)) if dm else 0.0
        if seg_dur > 0:
            seg_dur = max(3.0, min(10.0, seg_dur))

        motions = _field_blocks(part, "运镜")
        motion = (
            motions[0]
            if motions
            else "中近景稳机跟拍，节奏利落，禁止慢动作拖沓，不越轴、不翻面"
        )[:200]
        # 脚本若仍写「缓慢推进」，改成利落跟拍，避免成片拖沓
        if re.search(r"缓慢推进|慢推|慢动作", motion):
            motion = re.sub(
                r"缓慢推进|慢推|慢动作",
                "稳机跟拍",
                motion,
            )
            if "节奏利落" not in motion:
                motion = (motion.rstrip("。； ") + "，节奏利落，禁止慢动作拖沓。")[:200]
        scene_masters = _field_blocks(part, "场景母版")
        if not scene_masters:
            m = re.search(
                r"\[场景母版\]\s*\n(.+?)(?=\n\[|\n镜头\s*\d+|\Z)", part, re.S
            )
            if m:
                scene_masters = [m.group(1).strip()]
        master = (scene_masters[0] if scene_masters else "")[:100]

        lens_parts = [
            lp
            for lp in re.split(r"(?=^镜头\s*\d+)", part, flags=re.M)
            if re.match(r"^镜头\s*\d+", lp.strip())
        ]
        if not lens_parts:
            lens_parts = [part]
        parsed_lenses = [_parse_lens_block(lp) for lp in lens_parts]

        voices: List[Dict[str, Any]] = []
        action_vis = ""
        t_cursor = 0.0
        last_speech_end = 0.0
        for cur in parsed_lenses:
            lens_dur = max(1.0, float(cur.get("duration") or 5.0))
            vis_raw = (cur.get("visual") or "").strip()
            if vis_raw and len(vis_raw) > len(action_vis):
                # 优先保留完整「画面描述」，勿被场景母版盖住/截断
                action_vis = vis_raw
            for v in cur.get("voices") or []:
                vv = dict(v)
                text_v = (vv.get("text") or "").strip()
                if not text_v:
                    continue
                st = round(t_cursor + 0.35, 2)
                vv["start_sec"] = st
                vv["text"] = text_v
                voices.append(vv)
                last_speech_end = max(
                    last_speech_end, st + _estimate_speak_sec(text_v) + 0.35
                )
            t_cursor += lens_dur

        # 画面：动作/台词描述优先，场景母版只作短锁定
        vis_bits: List[str] = []
        if action_vis:
            vis_bits.append(action_vis[:420])
            if master:
                vis_bits.append(master[:72])
        elif master:
            vis_bits.append(master[:120])

        if voices:
            # 充实/中等对白尊重脚本时长（约 6–9s）；仅极短句易空声才收短
            need = max(3.0, last_speech_end + 0.45)
            if seg_dur >= 3.0:
                dur = float(seg_dur)
                if need < 4.0 and dur > need + 2.0:
                    dur = max(3.0, min(5.0, need + 0.6))
                else:
                    dur = max(3.0, min(10.0, dur))
            else:
                dur = need
            dur = max(3.0, min(10.0, dur))
            overrun = last_speech_end - (dur - 0.35)
            if overrun > 0.05:
                for vv in voices:
                    vv["start_sec"] = round(
                        max(0.3, float(vv.get("start_sec") or 0.3) - overrun), 2
                    )
            visual = _dedupe_visual_clauses(
                "；".join(
                    x
                    for x in (
                        *vis_bits[:2],
                        "只念列出的对白，说完立刻停口禁止续念胡言；"
                        "停口后继续走动、呼吸与环境声至镜尾，禁止静默空白与慢动作拖沓。",
                    )
                    if x
                ),
                max_len=420,
            )
            amb = "quiet natural outdoor/room tone under speech, no hum, no dead air"
        else:
            # 纯动作：脚本写了 6s+ 可保留；轻内容才压到 3–5s
            raw = seg_dur if seg_dur >= 3.0 else (t_cursor if t_cursor >= 3.0 else 4.0)
            if float(raw) >= 6.0:
                dur = max(6.0, min(10.0, float(raw)))
            else:
                dur = max(3.0, min(5.0, float(raw)))
            dur = max(3.0, min(10.0, dur))
            visual = _dedupe_visual_clauses(
                "；".join(
                    x
                    for x in (
                        *vis_bits[:2],
                        "全程闭口无对白；动作干脆+环境声铺满整段，禁止冷场僵住与慢动作拖沓。",
                    )
                    if x
                ),
                max_len=400,
            )
            visual = _scrub_silent_visual(visual)
            amb = "quiet natural outdoor dusk wind, soft fabric rustle, no speech, no dead air"

        visual = _scrub_facing_contradictions(visual)
        visual = _enrich_visual_lighting_motion(
            visual,
            scene_name="",
            title=title_short,
            part_text=part,
            has_dialogue=bool(voices),
        )
        shots.append(
            {
                "shot_num": len(shots) + 1,
                "duration_sec": float(dur),
                "title": title_short[:80],
                "visual": visual,
                "motion": motion,
                "voices": voices,
                "continuity": "承接同场站位，不换景、不翻面。",
                "ambience": amb,
            }
        )
    return shots


