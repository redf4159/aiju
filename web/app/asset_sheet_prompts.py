# -*- coding: utf-8 -*-
"""资产设定图（角色/场景/道具）统一：外表/风格/多视图组合结构。"""
from __future__ import annotations

from typing import Literal, Optional, Tuple

AssetKind = Literal["character", "scene", "prop"]

DEFAULT_ART_STYLE = "半写实3D国漫"

CHARACTER_SHEET_LAYOUT = (
    "单张横向16:9组合图，整图恰好三个视图。"
    "左侧约48%并排放置正面服装全身视图和背面完整全身视图，右侧约52%只放置一幅正面胸像，以自然留白区分区域。"
    "左侧第一幅身体严格正对镜头，在颈部附近干净裁切省略头部，可保留一小段自然颈部，不出现下巴、脸、耳朵、头皮或任何头发；"
    "皮肤完整，无创伤或解剖断面。双肩水平、身体直立，双臂自然下垂，双手空置且清楚，完整展示服装正面、双腿和鞋子，双脚脚底完整入画。"
    "左侧第二幅同一人物严格180度背对镜头，完整保留头部及发型，双肩水平、双臂自然下垂，完整展示服装背面、双手、双腿和鞋子，双脚脚底完整入画；"
    "不回头、不露侧脸、不成为侧后方或三分之四背面。"
    "左侧正背面使用同一个身体模型、同一真实身体尺寸和完全相同的镜头缩放；"
    "肩线、肘部、手腕、腰线、裆部、膝盖、脚踝与双脚底逐项水平对齐，肩宽、胯宽、躯干长度、腿长、臂长、站姿宽度、鞋子真实尺寸及底部留白一致。"
    "衣服遮住的基准只约束内部比例，不绘制透视骨骼或辅助线；正背鞋形允许正常观察方向差异。"
    "背面头部从对齐的肩线上方自然延伸，头顶高于无头正面的颈部截口，不为容纳头部缩小背面人物，也不强求两幅可见外框总高度相同。"
    "右侧仅一幅胸部以上的严格正面肖像，身体与脸完全正对镜头，双肩等高；胸骨、颈部、下巴、鼻梁与胸像区域中轴位于同一垂直线上，双眼水平，双耳透视基本对称。"
    "头部自然竖直，不歪头、低头、仰头或侧转，身体不倾斜、斜肩或扭转。"
    "完整保留发顶与头部两侧发型轮廓，下边缘停在胸部，不出现腰部或腰带；无侧脸、三分之四脸、第二张头像或附加视图。"
    "三个视图均为同一人物、同一年龄和同一套造型，身体比例、可见发型、衣装结构、材质、颜色、纹样、配件及鞋子一致。"
    "背景为干净、均匀、无色偏的中性纯灰色，三个视图背景相同，无环境物件、纹理或明显渐变；"
    "各视图朝向镜头的可见面采用正前方大面积中性柔光与轻微正面补光，清楚显示身份和衣装；无顶光、头顶集中高光、轮廓光、环境光效或暗角。"
    "图中不设置标题区、人物信息区、文字、标签、分隔线、边框、logo、水印、面板网格、地面线、脚下投影或展示台。"
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
    "{art_style}，按该人物身份呈现可信的三维人物比例、细腻但克制的面部塑造、服装织物质感与自然衣褶；"
    "不采用写实摄影风格，维持清晰国漫造型语言。"
)

SCENE_STYLE_TEMPLATE = (
    "{art_style}，空间结构清楚、材质可读、光影统一；不采用写实摄影棚感，维持清晰国漫场景语言。"
)

PROP_STYLE_TEMPLATE = (
    "{art_style}，物件比例准确、材质与磨损可读；不采用电商白底摄影风，维持清晰国漫道具语言。"
)

SHEET_NEGATIVE = (
    "文字, 字幕, 水印, logo, 边框, 分格线, 标题栏, 信息面板, 地面线, 脚下投影, 展示台, "
    "写实摄影, 真人照片, 二次元Q版, 扭曲肢体, 多余手指, 侧脸胸像, 三分之四背面"
)


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


def default_style_line(kind: AssetKind, art_style: str = DEFAULT_ART_STYLE) -> str:
    style = (art_style or DEFAULT_ART_STYLE).strip() or DEFAULT_ART_STYLE
    if kind == "scene":
        return SCENE_STYLE_TEMPLATE.format(art_style=style)
    if kind == "prop":
        return PROP_STYLE_TEMPLATE.format(art_style=style)
    return CHARACTER_STYLE_TEMPLATE.format(art_style=style)


def layout_for(kind: AssetKind) -> str:
    if kind == "scene":
        return SCENE_SHEET_LAYOUT
    if kind == "prop":
        return PROP_SHEET_LAYOUT
    return CHARACTER_SHEET_LAYOUT


def build_structured_prompt(
    *,
    kind: AssetKind,
    name: str,
    appearance: str,
    art_style: str = DEFAULT_ART_STYLE,
    style_line: Optional[str] = None,
    layout: Optional[str] = None,
    trait_extra: str = "",
) -> str:
    """组装「外表/风格/图片展示结构」三段式生图提示。"""
    app = (appearance or "").strip()
    if _has_layout_section(app):
        # 已是完整结构，仅保证点名主体
        head = f"{'角色' if kind == 'character' else ('场景' if kind == 'scene' else '道具')}：{name}\n"
        return (head + app).strip() if name and name not in app[:40] else app

    style = (style_line or default_style_line(kind, art_style)).strip()
    lay = (layout or layout_for(kind)).strip()
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
) -> Tuple[str, str]:
    """供 OhMyGPT 出设定图：返回 (prompt, negative)。"""
    raw = (visual_prompt or "").strip() or (role_note or "").strip() or name
    # 特点位/状态图：在标准三视图外附加说明，仍保持灰底设定图语言
    trait = ""
    angle = (angle_label or "").strip()
    if angle and angle not in ("正面", "基础", "默认", name):
        trait = f"本张侧重「{angle}」特点位或状态差异，仍保持同一主体可识别特征；若非基础三视图，则在同一灰底上清晰展示该特点区域或该状态全身/局部。"
    if (extra or "").strip():
        trait = (trait + " " + extra.strip()).strip()

    prompt = build_structured_prompt(
        kind=kind,
        name=name,
        appearance=raw,
        art_style=art_style,
        trait_extra=trait,
    )
    # 强制横屏设定图意图
    prompt = f"{prompt}\n画幅：横向16:9设定组合图，禁止竖屏。"
    return prompt, SHEET_NEGATIVE
