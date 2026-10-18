# -*- coding: utf-8 -*-
"""资产设定图（角色/场景/道具）统一：外表/风格/多视图组合结构。

角色标准（与制作台一致性参考图对齐）：
- 基础态与每一个「状态/造型」的主设定图必须是「单张合集」：
  同一张图内同时包含全身四向 + 头部三向 + 局部特写，缺一不可；
- 禁止只出全身没有头部特写；禁止只出头部没有全身前后左右。
- 可选再单独补「头部加强图」，但不能用它替代主设定合集图。
"""
from __future__ import annotations

import re
from typing import Literal, Optional, Tuple

AssetKind = Literal["character", "scene", "prop"]
SheetMode = Literal["body", "head"]

DEFAULT_ART_STYLE = "半写实3D国漫"

# 主设定：一张图内 = 全身四向 + 头部三向 + 局部特写
CHARACTER_SHEET_LAYOUT = (
    "单张横向16:9角色设定合集图，专供后期人物一致性锁定；整图必须同时包含全身、头部与局部特写，缺一不可。"
    "布局建议（可微调间距，但内容不可缺）："
    "上方约55%并排四幅完整全身站姿——①正面全身（带头，头顶到鞋底完整）、"
    "②左侧面全身（纯左侧，耳与鼻梁侧轮廓清晰）、"
    "③背面全身（严格180度背对，发型发饰背面完整，不回头）、"
    "④右侧面全身（纯右侧，耳与鼻梁侧轮廓清晰）；"
    "下方约45%从左到右：⑤正面头肩特写、⑥纯左侧脸头肩特写、⑦纯右侧脸头肩特写，"
    "以及⑧一到两幅局部特写（眼部/唇部/关键发饰或服装纹样配饰特写，突出角色辨识特征）。"
    "所有视图为同一人物、同一年龄、同一套造型与同一张脸；全身四向双臂自然下垂、双手空置、脚底完整入画，比例与镜头缩放一致。"
    "头部与局部特写必须把五官骨相、发型发饰、妆面与标志性配饰画清楚；禁止换脸。"
    "禁止整图只有全身没有头部；禁止整图只有头像没有全身前后左右；禁止裁掉头的全身；禁止只出正背两幅。"
    "背景为干净均匀中性纯灰，各视图背景一致；柔光正面补光；无文字、标签、边框、水印、分格线、展示台投影。"
)

# 可选：头部加强图（不能替代主设定合集）
CHARACTER_HEAD_SHEET_LAYOUT = (
    "单张横向16:9头部加强设定图（附加，不能代替含全身的主设定合集图）。"
    "从左到右必须并排三幅头肩特写："
    "①严格正面、②纯左侧脸、③纯右侧脸；"
    "可选再加一到两幅局部特写（眼/唇/发饰）。"
    "三幅尺度接近，双眼/鼻尖高度基本对齐，裁切下缘在锁骨或上胸。"
    "必须突出脸型骨相、眼型眉形、鼻唇、肤色妆面、发型与发饰；同一张脸，禁止换脸。"
    "背景干净均匀中性纯灰，柔光正面补光，无文字、标签、边框、水印、分格线。"
)

SCENE_SHEET_LAYOUT = (
    "单张横向16:9组合图，整图恰好三个视图。"
    "左侧约48%并排放置正面宽景母景与略偏侧的纵深母景，右侧约52%放置同一空间的关键结构特写（门廊/梁柱/陈设锚点之一）。"
    "三视图同一地点、同一时段、同一光影与材质体系；无主要角色清晰人脸，无文字无水印无边框。"
    "背景干净统一，禁止拼贴分格线、禁止信息面板。"
)

PROP_SHEET_LAYOUT = (
    "单张横向16:9组合图，整图恰好三个视图。"
    "左侧约48%并排放置道具正面完整视图与180度背面（或底面）完整视图，右侧约52%放置关键细节特写（纹样/磨损/机关/铭文等）。"
    "三视图同一物件、同一材质与比例，干净中性灰底，无人物无文字无边框无展示台投影。"
)

CHARACTER_STYLE_TEMPLATE = (
    "{art_style}，按该人物身份呈现可信人物比例、细腻但克制的面部塑造、服装织物质感与自然衣褶；"
    "风格与本剧画风一致，清晰统一，避免杂乱拼贴。"
)

SCENE_STYLE_TEMPLATE = (
    "{art_style}，空间结构清楚、材质可读、光影统一；风格与本剧画风一致，避免摄影棚白底杂乱拼贴。"
)

PROP_STYLE_TEMPLATE = (
    "{art_style}，物件比例准确、材质与磨损可读；风格与本剧画风一致，避免电商白底杂乱拼贴。"
)

SHEET_NEGATIVE_BASE = (
    "文字, 字幕, 水印, logo, 边框, 分格线, 标题栏, 信息面板, 地面线, 脚下投影, 展示台, "
    "写实摄影, 真人照片, 扭曲肢体, 多余手指, "
    "单人单角度整图, 只出全身无头部特写, 只出头部无全身, 只出正背无左右, "
    "无头正面全身, 用胸像代替全身侧面, 拼贴杂乱背景"
)

_LAYOUT_SECTION_RE = re.compile(
    r"(?:^|\n)\s*图片展示结构\s*[：:].*",
    re.S,
)
_STYLE_SECTION_RE = re.compile(
    r"(?:^|\n)\s*风格描述\s*[：:][^\n]*(?:\n(?!\s*(?:特点与状态补充|图片展示结构|画幅)\s*[：:])[^\n]*)*",
    re.M,
)
_APPEARANCE_SECTION_RE = re.compile(
    r"(?:角色|场景|道具)外表描述\s*[：:]\s*([\s\S]*?)(?=\n\s*风格描述\s*[：:]|\n\s*特点与状态补充\s*[：:]|\n\s*图片展示结构\s*[：:]|\n\s*画幅\s*[：:]|\Z)",
    re.M,
)


def is_chibi_art_style(art_style: str) -> bool:
    s = art_style or ""
    return any(k in s for k in ("萌", "Q版", "q版", "卡通", "童趣", "可爱", "幼龄"))


def sheet_negative(art_style: str = "") -> str:
    """按画风调整负向词：萌娃/Q版不再误伤，并抑制半写实国漫成人脸。"""
    parts = [SHEET_NEGATIVE_BASE]
    if is_chibi_art_style(art_style):
        parts.append(
            "半写实3D国漫, 成人写实国漫, 成熟成人比例, 写实五官, "
            "冷峻厚重建模, 次世代写实皮肤"
        )
    else:
        parts.append("二次元Q版, 幼齿夸张头身比")
    return ", ".join(parts)


# 兼容旧引用
SHEET_NEGATIVE = SHEET_NEGATIVE_BASE


def sheet_canvas(width: int = 1280, height: int = 720) -> Tuple[int, int]:
    """设定图固定横屏 16:9。"""
    w = max(640, int(width or 1280))
    h = max(360, int(height or 720))
    if w < h:
        w, h = h, w
    # snap 16
    w = max(640, (w // 16) * 16)
    h = max(360, (h // 16) * 16)
    return w, h


def _has_layout_section(text: str) -> bool:
    t = text or ""
    return "图片展示结构" in t or "三个视图" in t or "16:9组合图" in t or "16:9 组合图" in t


def strip_layout_section(text: str) -> str:
    """去掉旧的图片展示结构段，便于强制换用现行标准布局。"""
    t = (text or "").strip()
    if not t:
        return ""
    t2 = _LAYOUT_SECTION_RE.sub("", t).strip()
    # 顺手去掉尾部画幅强制句，避免重复
    t2 = re.sub(r"(?:^|\n)\s*画幅\s*[：:].*$", "", t2, flags=re.S).strip()
    return t2 or t


def strip_style_section(text: str) -> str:
    """去掉已烘焙的「风格描述」段，避免旧画风残留进外表描述。"""
    t = (text or "").strip()
    if not t:
        return ""
    return _STYLE_SECTION_RE.sub("", t).strip()


def extract_appearance_body(text: str) -> str:
    """从三段式提示中只取外表描述，丢掉旧风格/布局。"""
    t = (text or "").strip()
    if not t:
        return ""
    m = _APPEARANCE_SECTION_RE.search(t)
    if m:
        return (m.group(1) or "").strip()
    t = strip_layout_section(strip_style_section(t))
    t = re.sub(r"^(?:角色|场景|道具)\s*[：:]\s*", "", t).strip()
    return t


def default_style_line(kind: AssetKind, art_style: str = DEFAULT_ART_STYLE) -> str:
    style = (art_style or DEFAULT_ART_STYLE).strip() or DEFAULT_ART_STYLE
    if is_chibi_art_style(style):
        if kind == "scene":
            return (
                f"{style}，温馨柔彩古风场景，圆润造型与简化建筑细节，"
                f"童话插画感，避免半写实3D国漫厚重建模。"
            )
        if kind == "prop":
            return (
                f"{style}，可爱圆润道具造型，色彩明快，卡通质感，"
                f"避免半写实3D国漫写实材质。"
            )
        return (
            f"{style}，大头小身可爱比例，圆润五官，软萌表情，古风童装配色柔和，"
            f"儿童向卡通插画感，避免半写实3D国漫成人脸与写实皮肤。"
        )
    if kind == "scene":
        return SCENE_STYLE_TEMPLATE.format(art_style=style)
    if kind == "prop":
        return PROP_STYLE_TEMPLATE.format(art_style=style)
    return CHARACTER_STYLE_TEMPLATE.format(art_style=style)


def layout_for(kind: AssetKind, sheet_mode: SheetMode = "body") -> str:
    if kind == "scene":
        return SCENE_SHEET_LAYOUT
    if kind == "prop":
        return PROP_SHEET_LAYOUT
    if sheet_mode == "head":
        return CHARACTER_HEAD_SHEET_LAYOUT
    return CHARACTER_SHEET_LAYOUT


def is_base_angle_label(angle_label: str, name: str = "") -> bool:
    angle = (angle_label or "").strip()
    if not angle:
        return True
    base_tokens = {
        "正面",
        "基础",
        "默认",
        "基础三视图",
        "标准三视图",
        "三视图",
        "全身三视图",
        "设定图",
        "基础设定",
        "头部多角度",
        "头多角度",
        "AI·正面",
        "AI·基础",
        "AI·基础三视图",
        "AI·标准三视图",
        "AI·头部多角度",
    }
    if angle in base_tokens:
        return True
    if name and angle in {name, f"AI·{name}"}:
        return True
    return False


def build_structured_prompt(
    *,
    kind: AssetKind,
    name: str,
    appearance: str,
    art_style: str = DEFAULT_ART_STYLE,
    style_line: Optional[str] = None,
    layout: Optional[str] = None,
    trait_extra: str = "",
    sheet_mode: SheetMode = "body",
    force_canonical_layout: bool = True,
) -> str:
    """组装「外表/风格/图片展示结构」三段式生图提示。"""
    # 始终只用外表正文 + 当前画风重写风格段，杜绝旧「半写实3D国漫」残留
    app = extract_appearance_body(appearance or "")
    if force_canonical_layout:
        app = strip_layout_section(app)
        app = strip_style_section(app)

    style = (style_line or default_style_line(kind, art_style)).strip()
    lay = (layout or layout_for(kind, sheet_mode)).strip()
    trait = (trait_extra or "").strip()
    if kind == "character":
        label_a = "角色外表描述"
    elif kind == "scene":
        label_a = "场景外表描述"
    else:
        label_a = "道具外表描述"
    parts = [
        f"{label_a}：{app or name}",
        f"风格描述：{style}",
    ]
    if trait:
        parts.append(f"特点与状态补充：{trait}")
    parts.append(f"图片展示结构：{lay}")
    return "\n".join(parts)


def compose_sheet_api_prompt(
    *,
    kind: AssetKind,
    name: str,
    visual_prompt: str,
    role_note: str = "",
    art_style: str = DEFAULT_ART_STYLE,
    angle_label: str = "",
    extra: str = "",
    sheet_mode: SheetMode = "body",
) -> Tuple[str, str]:
    """供 OhMyGPT 出设定图：返回 (prompt, negative)。"""
    raw = extract_appearance_body(
        (visual_prompt or "").strip() or (role_note or "").strip() or name
    )
    trait_bits: list[str] = []
    angle = (angle_label or "").strip()
    mode: SheetMode = "head" if sheet_mode == "head" else "body"

    if mode == "head":
        trait_bits.append(
            "本张为头部三向设定图：只画头肩特写，不画全身站姿；"
            "必须含正面、纯左侧脸、纯右侧脸共三幅，突出五官与角色脸部特点；"
            "左右侧面要能清楚看到耳廓与鼻梁侧轮廓；"
            "脸型发饰必须与角色外表描述及全身基础设定为同一人。"
        )
    elif kind == "character" and angle and not is_base_angle_label(angle, name):
        trait_bits.append(
            f"本张为角色状态「{angle}」的设定合集图（单张同时含全身+头部+局部特写）。"
            f"必须包含：正面/左/背/右四幅带头全身，以及正面/左/右三幅头肩特写，再加局部特写；"
            f"全部呈现该状态下的服装发型与年龄感；禁止只出全身或只出头部。"
            f"【硬性同人】与基础态同一张脸、同一五官骨相；禁止换脸；角色辨识特征必须清晰可读。"
        )
    elif kind == "character":
        trait_bits.append(
            "本张为基础造型设定合集图：同一张图内必须同时有全身四向（正/左/背/右，带头）、"
            "头部三向（正/左/右头肩）以及局部特写（眼/唇/发饰或服装细节）；"
            "禁止只出全身没有头部特写，禁止只出头部没有全身。"
        )

    if (extra or "").strip():
        trait_bits.append(extra.strip())

    prompt = build_structured_prompt(
        kind=kind,
        name=name,
        appearance=raw,
        art_style=art_style,
        trait_extra=" ".join(trait_bits).strip(),
        sheet_mode=mode,
        force_canonical_layout=True,
    )
    # 强制横屏设定图意图
    if mode == "head":
        prompt = (
            f"{prompt}\n画幅：横向16:9头部加强设定图，禁止竖屏；"
            f"本张为附加头部图，不能代替含全身的主设定合集。"
        )
    else:
        prompt = (
            f"{prompt}\n画幅：横向16:9角色设定合集图（全身+头部+局部特写同框），"
            f"禁止竖屏，禁止单角度整图，禁止只出全身或只出头部。"
        )
    return prompt, sheet_negative(art_style)
