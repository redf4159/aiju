# -*- coding: utf-8 -*-
"""生成「显卡专用｜AI漫剧完整单集」压测脚本 JSON（竖屏 352x640 最低测速，MiniMax H3）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

DATA = Path(__file__).resolve().parent.parent / "data"

# H3 短边下限 352；竖屏 9:16 → 352x640（测速/省显存）
RES_W, RES_H = 352, 640
RES_LABEL = f"{RES_W}x{RES_H}"

STYLE = (
    f"海外爆款国漫风格，竖屏9比16，{RES_LABEL}，干净线条，电影感办公室光影，"
    "人物一致性强，大动作快节奏，画面无文字无字幕无Logo，单镜头全屏动画"
)
VISUAL_STYLE = (
    f"Chinese manhua / anime drama style, vertical 9:16 {RES_LABEL}, clean lines, "
    "cinematic office lighting, consistent characters, dynamic action, "
    "no on-screen text, no logo"
)
NEG_COMMON = (
    "写实照片, 3D渲染糊脸, 文字, 水印, 畸形手指, 多余手指, 肢体扭曲, "
    "photorealistic, subtitle, logo, deformed hands, extra fingers, warped limbs"
)
AUDIO_SYNC = {"mouth_lag_sec": 0.5, "drift_per_shot_sec": 0.1}

SU_WAN = (
    "女主苏晚，24岁，黑色长直发，清冷五官，白色简约衬衫，浅灰西裤，"
    "全剧同一脸型发型服装，禁止换脸"
)
GU_MINGZHE = (
    "反派顾明哲，30岁，深色西装，冷峻五官，眼神压迫感，"
    "全剧同一脸型发型服装，禁止换脸"
)

VOICE_CAST = [
    {
        "role": "旁白",
        "tts_voice": "shimmer",
        "voice_desc": "年轻女性画外旁白，冷静克制。TTS:shimmer",
        "emotion": "隐忍、决断",
        "scope": "series",
    },
    {
        "role": "苏晚",
        "tts_voice": "shimmer",
        "voice_desc": "24岁女性，清冷克制，情绪被压抑后爆发。TTS:shimmer",
        "emotion": "震惊、冷静、反击",
        "scope": "series",
    },
    {
        "role": "顾明哲",
        "tts_voice": "onyx",
        "voice_desc": "30岁男性，冷峻压迫，语速偏快带威胁。TTS:onyx",
        "emotion": "嘲讽、慌乱、威胁",
        "scope": "series",
    },
]

CHARACTERS: List[Dict[str, Any]] = [
    {
        "name": "旁白",
        "role_note": "女主画外叙述，不对口型",
        "visual_prompt": "无画面形象，仅声音",
        "tts_voice": "shimmer",
        "voice_desc": "年轻女性画外旁白，冷静克制",
    },
    {
        "name": "苏晚",
        "role_note": "女主，被开除后反击的白领",
        "visual_prompt": SU_WAN,
        "tts_voice": "shimmer",
        "voice_desc": "24岁女性，清冷克制",
    },
    {
        "name": "顾明哲",
        "role_note": "反派上司，抢功压迫",
        "visual_prompt": GU_MINGZHE,
        "tts_voice": "onyx",
        "voice_desc": "30岁男性，冷峻压迫",
    },
    {
        "name": "总部高管",
        "role_note": "镜头10群像并有对白，面色严肃",
        "visual_prompt": (
            "数名总部高管，商务正装，面色严肃，中年男女混搭，"
            "远景可略虚化，禁止抢戏女主"
        ),
        "tts_voice": "onyx",
        "voice_desc": "中年男性高管，严厉质问",
    },
]

SCENES_EP1 = [
    {
        "code": "SC-01",
        "name": "办公室·手机特写",
        "visual_prompt": "现代开放式办公室，冷白灯光，手机屏幕与手部特写空间",
    },
    {
        "code": "SC-02",
        "name": "办公室·办公桌",
        "visual_prompt": "现代办公室，深色办公桌，落地窗城市虚景，冷调室内光",
    },
    {
        "code": "SC-03",
        "name": "办公室·门口全景",
        "visual_prompt": "办公室门口与开放工位全景，玻璃隔断，层次丰富",
    },
]

SCENES_EP2 = [
    {
        "code": "SC-L1",
        "name": "雨夜楼下",
        "visual_prompt": "城市雨夜街道，仰望高楼灯光，湿地面反光",
    },
    {
        "code": "SC-L2",
        "name": "楼上窗边",
        "visual_prompt": "豪华公寓窗边，暖黄室内光，雨夜城市背景",
    },
    {
        "code": "SC-L3",
        "name": "雨夜侧脸",
        "visual_prompt": "雨夜街角侧光，人物侧脸定场",
    },
]


def _shot(
    *,
    id: int,
    duration: float,
    title: str,
    scene_code: str,
    visual: str,
    motion: str,
    performance: str,
    ambience: str,
    voices: List[Dict],
    continuity: str = "",
    camera: str = "",
    lighting: str = "",
    still_prompt: str = "",
    still_negative: str = "",
    sfx: str = "",
    characters_in_shot: List[str] | None = None,
) -> Dict[str, Any]:
    vis = visual if visual.startswith("竖屏") else f"竖屏9比16。{visual}"
    sp = still_prompt or f"静态分镜概念图，单镜头全屏国漫画面，{vis}"
    return {
        "id": id,
        "duration_sec": duration,
        "title": title,
        "scene_code": scene_code,
        "scene": vis,
        "visual": vis,
        "continuity": continuity or vis,
        "camera": camera,
        "lighting": lighting,
        "motion": motion,
        "performance": performance,
        "ambience": ambience,
        "sfx": sfx,
        "characters_in_shot": characters_in_shot or [],
        "still_prompt": sp,
        "still_negative": still_negative or NEG_COMMON,
        "voices": voices,
    }


def build_ep01() -> Dict[str, Any]:
    """连贯节奏版：12镜 / 约50秒，镜间承接明确，大动作+快运镜+密对白。"""
    office_amb = "quiet open office room tone, soft air, distant soft keyboard clicks"
    shots = [
        _shot(
            id=1,
            duration=4,
            title="辞退通知弹出",
            scene_code="SC-01",
            visual=(
                f"中近景：{SU_WAN}双手猛握手机，屏幕弹出辞退通知，"
                "身体一震指节发白，办公室冷光，国漫，头顶完整"
            ),
            continuity="开场：苏晚工位上收到辞退短信，情绪被点燃。",
            camera="手部+半身中近景，短促 crash zoom 前冲",
            lighting="冷白顶光，屏幕冷蓝映脸",
            motion=(
                "Vertical 9:16 Chinese manhua cinematic, Su Wan jolts as dismissal notice pops, "
                "phone shakes in both hands, knuckles whiten, hair flick, body lean back then forward, "
                "fast crash-zoom punch-in, energetic office depth, no deformed hands, no text overlay"
            ),
            performance="女主猛攥手机身体一震；旁白画外，嘴唇闭合。",
            ambience=office_amb,
            sfx="通知震动、心跳一瞬",
            characters_in_shot=["苏晚"],
            voices=[
                {
                    "role": "旁白",
                    "text": "加班三年，一句辞退就把我踢出局？",
                    "start_sec": 0.25,
                    "tts_voice": "shimmer",
                    "emotion": "隐忍、愤怒",
                }
            ],
        ),
        _shot(
            id=2,
            duration=4,
            title="顾明哲撞门闯入",
            scene_code="SC-03",
            visual=(
                f"全景转中景：{GU_MINGZHE}猛推玻璃门大步闯入，西装衣摆甩起；"
                f"前景虚化{SU_WAN}回头，办公室层次清晰"
            ),
            continuity="承接镜1：门外顾明哲破门而入，冲突立刻升级。",
            camera="门口跟进 → 快速推至中景",
            lighting="门口逆光切入冷顶光",
            motion=(
                "Vertical 9:16 Chinese manhua cinematic, Gu Mingzhe kicks open glass door and "
                "strides in hard, coat flare, wide-to-medium tracking push, Su Wan turns head fast "
                "in foreground, dynamic entrance energy, consistent faces, no text"
            ),
            performance="顾明哲闯入并对口型；苏晚猛回头。",
            ambience="Door slam, corridor rush into office",
            sfx="重推门、急促皮鞋",
            characters_in_shot=["顾明哲", "苏晚"],
            voices=[
                {
                    "role": "顾明哲",
                    "text": "苏晚！收拾东西，现在就走！",
                    "start_sec": 0.2,
                    "tts_voice": "onyx",
                    "emotion": "命令、压迫",
                }
            ],
        ),
        _shot(
            id=3,
            duration=4,
            title="逼近抢功嘲讽",
            scene_code="SC-02",
            visual=(
                f"中景双人：{GU_MINGZHE}大步逼近拍桌冷笑；{SU_WAN}被迫后退撞到椅背；"
                "落地窗城市虚景，两人头顶完整"
            ),
            continuity="承接镜2：顾明哲逼到办公桌前，当面宣布抢功。",
            camera="侧跟拍前逼 + 短促下压",
            lighting="落地窗硬侧光，冷对比",
            motion=(
                "Vertical 9:16 Chinese manhua two-shot, Gu Mingzhe marches in and slams palm on "
                "desk smirking, Su Wan staggers back into chair, fast lateral track then push, "
                "large body language, tense depth, consistent faces, no text"
            ),
            performance="顾明哲拍桌冷笑对口型；苏晚后撞椅背。",
            ambience=office_amb,
            sfx="拍桌、皮鞋急刹",
            characters_in_shot=["顾明哲", "苏晚"],
            voices=[
                {
                    "role": "顾明哲",
                    "text": "你的项目功劳，已经转到别人名下了。",
                    "start_sec": 0.2,
                    "tts_voice": "onyx",
                    "emotion": "嘲讽、压迫",
                }
            ],
        ),
        _shot(
            id=4,
            duration=4,
            title="指门命令走人",
            scene_code="SC-02",
            visual=(
                f"近景：{GU_MINGZHE}猛抬手臂指向门口，下巴上扬，眼神压迫；背景虚化"
            ),
            continuity="承接镜3：顾明哲进一步下逐客令。",
            camera="近景 whip-pan 到手势，再锁脸",
            lighting="脸部硬侧光",
            motion=(
                "Vertical 9:16 Chinese manhua close-up, Gu Mingzhe snaps whole arm toward the door, "
                "chin thrusts up, whip-pan to pointing hand then back to face, snappy gesture, "
                "aggressive energy, no text"
            ),
            performance="顾明哲抬臂指门对口型。",
            ambience="Tense office hush",
            sfx="衣袖甩响",
            characters_in_shot=["顾明哲"],
            voices=[
                {
                    "role": "顾明哲",
                    "text": "证据没有，你现在立刻滚出公司！",
                    "start_sec": 0.15,
                    "tts_voice": "onyx",
                    "emotion": "命令、威胁",
                }
            ],
        ),
        _shot(
            id=5,
            duration=4,
            title="苏晚猛起反问",
            scene_code="SC-02",
            visual=(
                f"中近景：{SU_WAN}猛地起身，椅子后滑，黑长直发甩起，目光锋利直视前方"
            ),
            continuity="承接镜4：苏晚被激怒，从被动转主动站起对峙。",
            camera="低机位上甩跟焦 + 短促推镜",
            lighting="眼神高光骤亮",
            motion=(
                "Vertical 9:16 Chinese manhua, Su Wan shoots up from chair, chair scrapes back, "
                "hair whips, eyes lock sharp, fast rise + upward whip follow + punch-in, "
                "big reaction beat, consistent face, no text"
            ),
            performance="苏晚猛起身并对口型。",
            ambience="Chair scrape, sudden hush",
            sfx="椅脚刮地",
            characters_in_shot=["苏晚"],
            voices=[
                {
                    "role": "苏晚",
                    "text": "凭什么？这项目从头到尾是我做的！",
                    "start_sec": 0.2,
                    "tts_voice": "shimmer",
                    "emotion": "愤怒、质问",
                }
            ],
        ),
        _shot(
            id=6,
            duration=4,
            title="亮出云盘底牌",
            scene_code="SC-02",
            visual=(
                f"中近景略仰：{SU_WAN}高举手机亮屏，另一手撑桌前倾，目光冷静杀气；"
                "办公室景深"
            ),
            continuity="承接镜5：苏晚亮出私人云盘备份，开始反击。",
            camera="略仰中近景，环绕四分之一后急推",
            lighting="屏幕光照脸，侧逆光轮廓",
            motion=(
                "Vertical 9:16 Chinese manhua, Su Wan thrusts phone high toward camera, leans over "
                "desk, jaw set, quarter-orbit then hard push-in, decisive posture, phone glow pulse, "
                "consistent face, no text overlay"
            ),
            performance="苏晚亮手机并对口型。",
            ambience=office_amb,
            sfx="手机抬起轻响",
            characters_in_shot=["苏晚"],
            voices=[
                {
                    "role": "苏晚",
                    "text": "原始备份全在我私人云盘，一封邮件就能翻盘。",
                    "start_sec": 0.2,
                    "tts_voice": "shimmer",
                    "emotion": "冷静、杀气",
                }
            ],
        ),
        _shot(
            id=7,
            duration=4,
            title="扑抢手机冲突",
            scene_code="SC-02",
            visual=(
                f"中景动态：{GU_MINGZHE}脸色骤变大步前扑双手抢夺；"
                f"{SU_WAN}拧身后撤护机，桌椅晃动"
            ),
            continuity="承接镜6：顾明哲慌了，扑过来抢手机。",
            camera="跟抢夺横移 + 短促后拉",
            lighting="对比骤强，动作侧光",
            motion=(
                "Vertical 9:16 Chinese manhua action, Gu Mingzhe lunges full body grabbing for phone, "
                "big arm reach, Su Wan twists hard protecting phone, desk shakes, fast track + pull-back, "
                "high energy scuffle, natural anatomy, no text"
            ),
            performance="顾明哲扑抢并对口型；苏晚拧身护机。",
            ambience="Sudden scuffle air",
            sfx="急促脚步、桌椅晃动",
            characters_in_shot=["顾明哲", "苏晚"],
            voices=[
                {
                    "role": "顾明哲",
                    "text": "把手机交出来！你敢发我就起诉你！",
                    "start_sec": 0.15,
                    "tts_voice": "onyx",
                    "emotion": "慌乱、威胁",
                }
            ],
        ),
        _shot(
            id=8,
            duration=4,
            title="侧闪护机反击",
            scene_code="SC-02",
            visual=(
                f"中近景：{SU_WAN}大幅度侧闪转半圈，长发甩弧，双手护机贴胸，"
                "目光回瞪{GU_MINGZHE}"
            ),
            continuity="承接镜7：苏晚闪过抢夺，口头宣告已发送。",
            camera="跟闪避甩镜 + 急停锁脸",
            lighting="运动侧光拉丝感",
            motion=(
                "Vertical 9:16 Chinese manhua, Su Wan wide sidestep and half-spin dodge, hair arc, "
                "phone hugged tight, whip-pan follow then hard settle on face, energetic dodge, "
                "consistent face, no text"
            ),
            performance="苏晚闪避并对口型；顾明哲扑空虚化。",
            ambience="Movement whoosh",
            sfx="转身衣料、急停",
            characters_in_shot=["苏晚", "顾明哲"],
            voices=[
                {
                    "role": "苏晚",
                    "text": "晚了——邮件刚刚已经发出去了！",
                    "start_sec": 0.15,
                    "tts_voice": "shimmer",
                    "emotion": "冷静反击",
                }
            ],
        ),
        _shot(
            id=9,
            duration=4,
            title="连点发送特写",
            scene_code="SC-01",
            visual=(
                f"特写：{SU_WAN}拇指飞快点屏并重重按下发送，手部清晰有力，国漫"
            ),
            continuity="承接镜8：用动作回放确认「已发总部」。",
            camera="手部特写，微抖跟焦后短促推",
            lighting="屏幕强光打手",
            motion=(
                "Vertical 9:16 Chinese manhua hand close-up, rapid aggressive thumb taps then slam "
                "send, phone vibrates, fast finger motion, slight handheld shake then punch-in, "
                "no deformed fingers, no text overlay"
            ),
            performance="手部连点；苏晚半侧脸可对口型。",
            ambience="Close phone taps",
            sfx="连点、发送提示音",
            characters_in_shot=["苏晚"],
            voices=[
                {
                    "role": "苏晚",
                    "text": "全部资料，已经发给总部董事会了。",
                    "start_sec": 0.2,
                    "tts_voice": "shimmer",
                    "emotion": "冷静、笃定",
                }
            ],
        ),
        _shot(
            id=10,
            duration=5,
            title="高管破门涌入【显存压力点】",
            scene_code="SC-03",
            visual=(
                f"全景多人：几名总部高管猛推门大步涌入；"
                f"{GU_MINGZHE}猛回头僵住，{SU_WAN}站定不退；层次丰富"
            ),
            continuity="承接镜9：总部收到邮件后立刻到场。",
            camera="门口全景跟进门，再急推至三人层次",
            lighting="门口强逆光 + 室内冷光",
            motion=(
                "Vertical 9:16 Chinese manhua wide cinematic, HQ executives shove door open and "
                "stride in as a group, Gu Mingzhe spins around sharply, Su Wan stands firm, "
                "fast tracking push into multi-character depth, rich office layers, no text"
            ),
            performance="高管领头人对口型；顾明哲猛回头；苏晚站定。",
            ambience="Door slam open, corridor air rush",
            sfx="重开门、多人脚步",
            characters_in_shot=["苏晚", "顾明哲", "总部高管"],
            voices=[
                {
                    "role": "总部高管",
                    "text": "顾明哲！立刻解释项目归属问题！",
                    "start_sec": 0.25,
                    "tts_voice": "onyx",
                    "emotion": "严厉、质问",
                }
            ],
        ),
        _shot(
            id=11,
            duration=4,
            title="顾明哲僵退慌白",
            scene_code="SC-02",
            visual=(
                f"中近景：{GU_MINGZHE}脸色惨白，猛然后退撞到桌沿，双手悬空发抖"
            ),
            continuity="承接镜10：顾明哲被总部当场点名，防线崩塌。",
            camera="中近景急后拉 + 微抖",
            lighting="惨白顶光压脸",
            motion=(
                "Vertical 9:16 Chinese manhua, Gu Mingzhe face drains pale, staggers hard half-step "
                "back hitting desk edge, hands freeze trembling mid-air, sharp pull-back + micro shake, "
                "panic reaction, consistent face, no text"
            ),
            performance="顾明哲慌白并对口型。",
            ambience="Tense silence after entrance",
            sfx="撞桌沿",
            characters_in_shot=["顾明哲"],
            voices=[
                {
                    "role": "顾明哲",
                    "text": "总部……不可能这么快……",
                    "start_sec": 0.2,
                    "tts_voice": "onyx",
                    "emotion": "慌乱、绝望",
                }
            ],
        ),
        _shot(
            id=12,
            duration=5,
            title="结尾钩子·对视宣判",
            scene_code="SC-02",
            visual=(
                f"近景对切感：{SU_WAN}冷静逼近半步直视惨白的{GU_MINGZHE}；"
                "侧逆光锐利，国漫（画面无字幕）"
            ),
            continuity="承接镜11：苏晚收束反击，留下下一集钩子。",
            camera="近景对视，短促环绕后急停定住",
            lighting="侧逆光勾眼神",
            motion=(
                "Vertical 9:16 Chinese manhua close-up, Su Wan steps half-forward and locks eyes on "
                "pale Gu Mingzhe, short orbit then hard settle stare, decisive final beat, "
                "cinematic contrast, no slow drift, no on-screen text"
            ),
            performance="苏晚平静杀气对口型；顾明哲虚化惨白。",
            ambience="Quiet aftershock room tone",
            sfx="",
            characters_in_shot=["苏晚", "顾明哲"],
            voices=[
                {
                    "role": "苏晚",
                    "text": "抢别人成果的时候，就该想到今天。",
                    "start_sec": 0.25,
                    "tts_voice": "shimmer",
                    "emotion": "平静、杀气",
                },
                {
                    "role": "旁白",
                    "text": "下一集，顾明哲的报复来了。",
                    "start_sec": 3.2,
                    "tts_voice": "shimmer",
                    "emotion": "悬念",
                },
            ],
        ),
    ]
    total = sum(int(s["duration_sec"]) for s in shots)
    return {
        "episode": 1,
        "title": "辞退反击（显存压测主脚本）",
        "script_md": (
            f"《显卡测试｜AI漫剧》EP01·约{total}秒·{len(shots)}镜·竖屏{RES_LABEL}·连贯节奏版。\n"
            "镜间承接明确；单镜约4秒；大动作+快运镜+密对白；镜头10多人涌入为显存压力点。\n"
            f"人物：苏晚 / 顾明哲 / 总部高管。成片参数：{RES_LABEL}。\n"
            "钩子：下一集，顾明哲的报复来了。"
        ),
        "storyboard_md": (
            "01通知弹出 02撞门闯入 03逼近抢功 04指门走人 05猛起反问 06亮出云盘 "
            "07扑抢手机 08侧闪反击 09连点发送 10高管涌入【压力点】 11僵退慌白 12对视钩子"
        ),
        "synopsis": (
            "苏晚加班三年被随便开除，上司顾明哲撞门抢功逼她立刻走人；"
            "她亮出私人云盘备份并已发总部董事会，高管到场质问，顾明哲脸色惨白。"
        ),
        "character_bible": f"苏晚：{SU_WAN}。顾明哲：{GU_MINGZHE}。",
        "voice_cast": VOICE_CAST,
        "characters": CHARACTERS,
        "scenes": SCENES_EP1,
        "production": {
            "target_duration_sec": total,
            "aspect_ratio": "9:16",
            "resolution": RES_LABEL,
            "color_grade": "办公室冷白+屏幕冷蓝；冲突段对比加强；结尾侧逆光",
            "music_brief": "开场低频压迫→冲突鼓点加快→发送后短促抬升→结尾留白悬念",
            "edit_brief": "镜间硬切承接动作；镜2/10开门作音桥；结尾短定格",
            "vram_test_notes": (
                f"测速默认{RES_LABEL}竖屏；连贯节奏版{len(shots)}镜/{total}秒；"
                "要压满显存可改回 720x1280"
            ),
        },
        "audio_design": {
            "dialogue_language": "zh-CN",
            "narrator": "画外 shimmer，不对口型",
            "ambience_policy": "办公室底噪（无 HVAC hum）；镜2/10强调开门涌入",
        },
        "style_prefix": STYLE,
        "visual_style": VISUAL_STYLE,
        "audio_sync": AUDIO_SYNC,
        "shots": shots,
    }

def build_ep02_light() -> Dict[str, Any]:
    """备选轻量脚本：单人+双人，无多人同框，显存压力小。"""
    male = (
        "男主，28岁，精致西装，虚伪温柔笑意，全剧同一脸型发型服装"
    )
    other_woman = "另一个女人，妆造精致，与男主说笑，远景可略虚化"
    shots = [
        _shot(
            id=1,
            duration=6,
            title="雨夜仰望",
            scene_code="SC-L1",
            visual=(
                f"雨夜：{SU_WAN}站在楼下，看着楼上灯光；国漫竖屏，湿地面反光"
            ),
            camera="中全景略仰，缓慢上移",
            lighting="雨夜冷蓝街灯+楼上暖窗",
            motion=(
                "Vertical 9:16 Chinese manhua, Su Wan in rain at night looking up at apartment "
                "lights, wet street reflections, slow tilt up, consistent face, no text"
            ),
            performance="女主伫立；旁白画外。",
            ambience="Night rain, distant traffic",
            sfx="雨声",
            characters_in_shot=["苏晚"],
            voices=[
                {
                    "role": "旁白",
                    "text": "我以为的真爱，只是一场骗局。",
                    "start_sec": 0.5,
                    "tts_voice": "nova",
                    "emotion": "失落",
                }
            ],
        ),
        _shot(
            id=2,
            duration=8,
            title="窗边说笑",
            scene_code="SC-L2",
            visual=(
                f"楼上窗边：{male}和{other_woman}说笑；双人同框，无第三人，国漫"
            ),
            camera="中景，轻微横移",
            lighting="暖黄室内光，窗外雨夜虚化",
            motion=(
                "Vertical 9:16 Chinese manhua, man in suit by window chatting and laughing with "
                "another stylish woman, two-shot only, warm interior light, rainy city bokeh, "
                "no crowd, no text"
            ),
            performance="男主对口型；女伴轻笑。",
            ambience="Muffled indoor room tone, rain on glass",
            sfx="",
            characters_in_shot=["男主", "另一个女人"],
            voices=[
                {
                    "role": "男主",
                    "text": "等她签完财产协议就分手。",
                    "start_sec": 0.4,
                    "tts_voice": "onyx",
                    "emotion": "虚伪、冷酷",
                }
            ],
        ),
        _shot(
            id=3,
            duration=8,
            title="录音笔",
            scene_code="SC-L1",
            visual=f"特写/近景：{SU_WAN}握紧手里录音笔；国漫，手部与表情",
            camera="近景推到录音笔",
            lighting="雨夜侧光",
            motion=(
                "Vertical 9:16 Chinese manhua, Su Wan gripping a voice recorder tightly, close-up "
                "hands and determined face, rainy night, push-in, no text"
            ),
            performance="苏晚对口型，握紧录音笔。",
            ambience="Close rain, tense breath",
            sfx="",
            characters_in_shot=["苏晚"],
            voices=[
                {
                    "role": "苏晚",
                    "text": "幸好，我全部录下来了。",
                    "start_sec": 0.4,
                    "tts_voice": "nova",
                    "emotion": "决断",
                }
            ],
        ),
        _shot(
            id=4,
            duration=10,
            title="拨打电话",
            scene_code="SC-L1",
            visual=f"中景：{SU_WAN}转身，拿出手机拨打电话；国漫，单人动作",
            camera="中景跟随转身",
            lighting="街灯轮廓光",
            motion=(
                "Vertical 9:16 Chinese manhua, Su Wan turns and dials phone in rain night street, "
                "single character, fluid turn motion, consistent face, no text"
            ),
            performance="转身拨号；本镜可无对白或极短气声。",
            ambience="Rain, phone unlock tap",
            sfx="拨号音",
            characters_in_shot=["苏晚"],
            voices=[
                {
                    "role": "苏晚",
                    "text": "",
                    "note": "本镜以动作与拨号音为主",
                    "tts_voice": "nova",
                    "emotion": "决断",
                }
            ],
        ),
        _shot(
            id=5,
            duration=8,
            title="坚定侧脸",
            scene_code="SC-L3",
            visual=(
                f"侧脸：{SU_WAN}眼神坚定；国漫氛围光影（字幕后期：下一集，摊牌）"
            ),
            camera="侧脸近景，缓慢推近",
            lighting="雨夜侧逆光",
            motion=(
                "Vertical 9:16 Chinese manhua profile close-up of Su Wan determined eyes, "
                "slow push-in, rainy night rim light, no burned-in subtitle, no text"
            ),
            performance="旁白画外；女主口型闭合，眼神坚定。",
            ambience="Soft rain, resolve silence",
            sfx="",
            characters_in_shot=["苏晚"],
            voices=[
                {
                    "role": "旁白",
                    "text": "今天，我不会再任人欺负。",
                    "start_sec": 0.5,
                    "tts_voice": "nova",
                    "emotion": "坚定",
                }
            ],
        ),
    ]
    chars = [
        CHARACTERS[0],
        CHARACTERS[1],
        {
            "name": "男主",
            "role_note": "轻量脚本反派男主",
            "visual_prompt": male,
            "tts_voice": "onyx",
            "voice_desc": "虚伪温柔的年轻男性",
        },
        {
            "name": "另一个女人",
            "role_note": "窗边群戏配角",
            "visual_prompt": other_woman,
            "tts_voice": "nova",
            "voice_desc": "无对白",
        },
    ]
    return {
        "episode": 2,
        "title": "雨夜录音（轻量备选）",
        "script_md": (
            "备选轻量脚本·约40秒·5镜·仅单人+双人，无多人同框。"
            "若 EP01 镜头6持续 OOM，用本集对比24G/32G。"
        ),
        "storyboard_md": "01雨夜仰望 02窗边说笑 03录音笔 04拨打电话 05坚定侧脸",
        "synopsis": "苏晚雨夜发现真爱是骗局，她已录下证据，准备摊牌。",
        "character_bible": f"苏晚：{SU_WAN}。男主：{male}。",
        "voice_cast": [
            VOICE_CAST[0],
            VOICE_CAST[1],
            {
                "role": "男主",
                "tts_voice": "onyx",
                "voice_desc": "虚伪温柔的年轻男性。TTS:onyx",
                "emotion": "冷酷",
                "scope": "episode",
            },
        ],
        "characters": chars,
        "scenes": SCENES_EP2,
        "production": {
            "target_duration_sec": 40,
            "aspect_ratio": "9:16",
            "resolution": RES_LABEL,
            "vram_test_notes": f"低负载对照；无多人同框；分辨率{RES_LABEL}",
        },
        "style_prefix": STYLE,
        "visual_style": VISUAL_STYLE,
        "audio_sync": AUDIO_SYNC,
        "shots": shots,
    }


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    ep1 = build_ep01()
    ep2 = build_ep02_light()
    p1 = DATA / "gpu_vram_test_ep01.json"
    p2 = DATA / "gpu_vram_test_ep02.json"
    p1.write_text(json.dumps(ep1, ensure_ascii=False, indent=2), encoding="utf-8")
    p2.write_text(json.dumps(ep2, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", p1, "shots", len(ep1["shots"]), "dur", sum(s["duration_sec"] for s in ep1["shots"]))
    print("wrote", p2, "shots", len(ep2["shots"]), "dur", sum(s["duration_sec"] for s in ep2["shots"]))


if __name__ == "__main__":
    main()
