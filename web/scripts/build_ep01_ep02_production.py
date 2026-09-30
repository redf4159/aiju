# -*- coding: utf-8 -*-
"""生成《冰境》EP01/EP02 完整 AI 短剧制作 JSON（人物/场景/分镜/视频/TTS/音效）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

DATA = Path(__file__).resolve().parent.parent / "data"

STYLE = (
    "东方玄幻史诗电影，冰雪世界，横屏16比9，真人写实电影摄影，"
    "体积光与暴雪，画面无文字无字幕无Logo，单镜全屏连续实拍感"
)
VISUAL_STYLE = (
    "Epic eastern fantasy cinema, realistic humans, ice and snow kingdom, "
    "dramatic lighting, 16:9 widescreen, no on-screen text"
)
NEG_COMMON = (
    "动漫, 卡通, 文字, 水印, 畸形脸, 多余手指, 白发王子, 银发, 刃雪城, 樱空释, 卡索, "
    "anime, subtitle, logo, deformed face, extra fingers"
)
AUDIO_SYNC = {"mouth_lag_sec": 0.6, "drift_per_shot_sec": 0.12}

VOICE_CAST = [
    {
        "role": "旁白",
        "tts_voice": "onyx",
        "voice_desc": "男性电影预告片式旁白，沉稳低沉。TTS:onyx",
        "emotion": "史诗、神秘",
        "scope": "series",
    },
    {
        "role": "凌川",
        "tts_voice": "echo",
        "voice_desc": "年轻男性，偏低沉干净有磁性。TTS:echo",
        "emotion": "疑惑、恐惧、震惊、决断",
        "scope": "series",
    },
    {
        "role": "神秘黑影",
        "tts_voice": "onyx",
        "voice_desc": "极低沉男性，空灵遥远，大空间混响。TTS:onyx",
        "emotion": "神秘、压迫",
        "scope": "series",
    },
    {
        "role": "玄清长老",
        "tts_voice": "onyx",
        "voice_desc": "年迈男性，沙哑而庄重，语速慢。TTS:onyx",
        "emotion": "忧虑、敬畏",
        "scope": "series",
    },
    {
        "role": "侍卫",
        "tts_voice": "echo",
        "voice_desc": "青年男性武士，简短有力。TTS:echo",
        "emotion": "紧张",
        "scope": "series",
    },
]

CHARACTERS: List[Dict[str, Any]] = [
    {
        "name": "旁白",
        "role_note": "全剧画外叙述，不对口型",
        "visual_prompt": "无画面形象，仅声音",
        "tts_voice": "onyx",
        "voice_desc": "男性电影预告片式旁白，沉稳低沉",
    },
    {
        "name": "凌川",
        "role_note": "冰境王国年轻王子，主线视角",
        "visual_prompt": (
            "20多岁东方男性王子，黑长发束部分发冠，深色东方玄幻王族长礼服与暗金纹饰，"
            "写实电影脸，五官端正，肤质自然，偏冷峻；全剧同一脸型发型服装，禁止换脸"
        ),
        "tts_voice": "echo",
        "voice_desc": "年轻男性，偏低沉干净有磁性",
    },
    {
        "name": "神秘黑影",
        "role_note": "裂缝中的超自然存在",
        "visual_prompt": (
            "人形轮廓由黑暗能量构成，无可见五官，边缘呈烟雾状，"
            "体型略高于常人，裂缝内逆光剪影，禁止清晰人脸"
        ),
        "tts_voice": "onyx",
        "voice_desc": "极低沉男性，大空间混响",
    },
    {
        "name": "玄清长老",
        "role_note": "王国祭司长老",
        "visual_prompt": (
            "60岁左右东方长者，灰白长发，深蓝绣银纹祭司长袍，"
            "持冰晶法杖，皱纹清晰，真人写实"
        ),
        "tts_voice": "onyx",
        "voice_desc": "年迈男性，沙哑庄重",
    },
    {
        "name": "侍卫",
        "role_note": "王宫武士，群像时可虚化",
        "visual_prompt": "青年东方武士，冰甲轻铠，面部在远景中可略虚化",
        "tts_voice": "echo",
        "voice_desc": "青年男性，简短有力",
    },
]

SCENES_EP1 = [
    {"code": "SC-01", "name": "冰境王城远景", "visual_prompt": "无尽雪原上的巨大冰雪王城，中央冰晶城堡，远山阴云暴雪"},
    {"code": "SC-02", "name": "冰晶王宫长廊", "visual_prompt": "巨大冰柱长廊，烛火与冷暖对比，地面反光"},
    {"code": "SC-03", "name": "梦境冰原", "visual_prompt": "无边冰原，苍白天空，远方雪山，强风"},
    {"code": "SC-04", "name": "梦境·黑神殿", "visual_prompt": "残破黑色巨石神殿从冰原升起，暴风雪"},
    {"code": "SC-05", "name": "梦境·天际裂缝", "visual_prompt": "神殿上空巨大黑色空间裂缝，暗能量，雪向上飞卷"},
    {"code": "SC-06", "name": "凌川寝殿", "visual_prompt": "古代王室卧室，月光透窗，木质与冰饰结合"},
    {"code": "SC-07", "name": "现实·王宫露台", "visual_prompt": "王宫高窗/露台，可俯瞰王城夜空与裂缝"},
]

SCENES_EP2 = SCENES_EP1 + [
    {"code": "SC-08", "name": "议事冰殿", "visual_prompt": "圆形冰壁议事厅，中央冰台，长老与王子"},
    {"code": "SC-09", "name": "王城街道", "visual_prompt": "冰雪街道，民众仰望天空，黑雪飘落"},
    {"code": "SC-10", "name": "城墙与垛口", "visual_prompt": "高墙俯瞰王城，裂缝占天空大半"},
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
    vis = visual if visual.startswith("横屏") else f"横屏16比9。{visual}"
    sp = still_prompt or f"静态分镜概念图，单镜头全屏电影画面，{vis}"
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
    shots = [
        _shot(
            id=1,
            duration=6,
            title="冰境王城",
            scene_code="SC-01",
            visual="史诗环境定场（无人物、无主角）：无尽雪原上的巨大冰雪王城，中央冰晶城堡，远山覆雪，阴云压顶，暴雪缓缓飘落，神秘庄严。",
            motion=(
                "Epic cinematic fantasy establishing shot, NO people NO hero NO prince in frame, "
                "only architecture and landscape, ancient kingdom in snow and ice, enormous ice castle, "
                "distant mountains, heavy clouds, endless snowfall, slow dolly forward, 24fps, no text."
            ),
            performance="无人物出镜；旁白画外，出镜者嘴唇闭合。雪与环境粒子缓慢飘动。",
            ambience="Soft winter wind, distant snowstorm, gentle air, 暴雪风雪",
            camera="超广角大全景，慢速推轨，地平线水平，24fps",
            lighting="阴云冷蓝顶光，城堡暖窗点光，体积雪雾",
            sfx="远风低鸣，极轻钟鸣尾音（无电流无嗡鸣）",
            characters_in_shot=[],
            still_negative=NEG_COMMON + ", 人物特写, 王子, 人像, hero portrait",
            voices=[
                {
                    "role": "旁白",
                    "text": "很久以前，在世界的尽头，有一座被冰雪覆盖的王国。",
                    "start_sec": 0.8,
                    "tts_voice": "onyx",
                    "voice_desc": "男性电影预告片式旁白，沉稳低沉。TTS:onyx 情绪：史诗、神秘。",
                    "emotion": "史诗、神秘",
                }
            ],
        ),
        _shot(
            id=2,
            duration=5,
            title="暴雪王宫",
            scene_code="SC-02",
            visual="巨大冰晶宫殿长廊，冰柱与烛火冷暖对比；主角凌川（20多岁、黑长发、深色王族礼服）独自站在长廊尽头，全片同一脸型。",
            motion=(
                "Young male prince Ling Chuan, 20s, long black hair, dark eastern fantasy royal robe, "
                "SAME face and costume as entire series, standing alone in gigantic ice palace corridor, "
                "crystalline pillars, candlelight, slow tracking shot, realistic cinema, no text."
            ),
            performance="凌川静止伫立，微呼吸；无对白口型。",
            ambience="Quiet palace hall, wind through ice corridor, candle room tone, 王宫长廊殿内混响, hall chamber",
            camera="中远景侧轨，缓慢横移，浅景深",
            lighting="烛火暖色与冰墙冷蓝对比",
            sfx="衣摆轻响，烛火噼啪，持续环境底噪",
            characters_in_shot=["凌川"],
            continuity="紧接王城空镜，进入同一王城内部；凌川服装与全剧一致。",
            voices=[
                {
                    "role": "凌川",
                    "text": "",
                    "note": "本镜无对白，仅表演/环境",
                    "tts_voice": "echo",
                    "voice_desc": "年轻男性，偏低沉干净有磁性。TTS:echo",
                    "emotion": "疑惑、恐惧、震惊",
                }
            ],
        ),
        _shot(
            id=3,
            duration=6,
            title="冰原上的凌川",
            scene_code="SC-03",
            visual="梦境：无边冰原，凌川独自站立，远方雪山，苍白天空，孤独神秘。",
            motion=(
                "Young prince Ling Chuan alone on endless frozen wasteland, black hair in strong wind, "
                "dark robe, distant snow mountains, pale sky, cinematic wide shot, slow camera drift, "
                "realistic, SAME Ling Chuan face, no text."
            ),
            performance="迎风站立；旁白画外，凌川口型不动。",
            ambience="Strong cold wind, low-frequency atmosphere",
            camera="广角远景，轻微手持感漂移",
            lighting="高调灰白梦境光，边缘略过曝",
            characters_in_shot=["凌川"],
            voices=[
                {
                    "role": "旁白",
                    "text": "年轻的王子凌川，却从小被一个相同的噩梦纠缠。",
                    "start_sec": 0.6,
                    "tts_voice": "onyx",
                    "voice_desc": "男性电影预告片式旁白，沉稳低沉。TTS:onyx",
                    "emotion": "史诗、神秘",
                }
            ],
        ),
        _shot(
            id=4,
            duration=5,
            title="黑色雪花",
            scene_code="SC-03",
            visual="特写：白雪中几片黑色雪花飘落，一片落在凌川手背并化为 dark smoke。",
            motion=(
                "Extreme close-up black snowflakes among white snow, one lands on Ling Chuan hand, "
                "melts into dark smoke, shallow DOF, eastern dark fantasy, realistic texture, no text."
            ),
            performance="手部微颤；无台词口型。",
            ambience="Close icy wind, subtle supernatural vibration",
            camera="微距特写，浅景深",
            lighting="冷侧光，黑色雪花高对比",
            characters_in_shot=["凌川（手）"],
            voices=[
                {
                    "role": "凌川",
                    "text": "",
                    "note": "本镜无对白",
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo",
                    "emotion": "疑惑",
                }
            ],
        ),
        _shot(
            id=5,
            duration=7,
            title="黑色神殿",
            scene_code="SC-04",
            visual="凌川抬头，远景巨大残破黑色神殿从冰原中显现，凌川在前景渺小。",
            motion=(
                "Massive ruined black temple emerging from frozen wasteland, snowstorm, "
                "tiny Ling Chuan in foreground looking up, epic scale, volumetric fog, no text."
            ),
            performance="凌川仰望，身体前倾；无对白。",
            ambience="Deep rumble, powerful wind, mysterious resonance",
            camera="低角度广角，缓慢 tilt up",
            characters_in_shot=["凌川"],
            voices=[
                {
                    "role": "凌川",
                    "text": "",
                    "note": "无对白",
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo",
                    "emotion": "恐惧",
                }
            ],
        ),
        _shot(
            id=6,
            duration=6,
            title="黑色裂缝",
            scene_code="SC-05",
            visual="神殿上空巨大黑色空间裂缝打开，暗能量扩散，雪向上飞卷，末世感。",
            motion=(
                "Gigantic black dimensional crack opening above ruined black temple, dark energy, "
                "clouds distorted, snow blowing upward, apocalyptic scale, tilt up, VFX realistic, no text."
            ),
            performance="无人物特写；纯环境剧变。",
            ambience="Deep dimensional rumble, distorted wind, energy surge",
            camera="急速 tilt up 至裂缝中心",
            sfx="空间撕裂低频，风反向呼啸",
            characters_in_shot=[],
            voices=[
                {
                    "role": "环境",
                    "text": "",
                    "note": "纯 SFX，无 TTS",
                    "tts_voice": "",
                    "voice_desc": "不用 TTS",
                    "emotion": "",
                }
            ],
        ),
        _shot(
            id=7,
            duration=6,
            title="凌川靠近裂缝",
            scene_code="SC-05",
            visual="背跟拍凌川走向天际裂缝，黑袍长发在风中剧烈飘动，身后黑色神殿。",
            motion=(
                "Rear tracking shot Ling Chuan walking toward black crack in sky, robe and hair in violent wind, "
                "snow swirl, temple behind, epic fantasy cinema, realistic gait, no text."
            ),
            performance="缓步前行；约1.0秒起低声台词，口型与「又是这个梦……」同步。",
            ambience="Strong wind, snow swirl, low rumble",
            camera="背跟中景，稳定器，风速感强",
            characters_in_shot=["凌川"],
            voices=[
                {
                    "role": "凌川",
                    "text": "又是这个梦……",
                    "start_sec": 1.0,
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo 情绪：疲惫、恐惧",
                    "emotion": "疲惫、恐惧",
                }
            ],
        ),
        _shot(
            id=8,
            duration=7,
            title="神秘黑影",
            scene_code="SC-05",
            visual="裂缝内模糊人形黑影，无五官，凌川在下方仰望恐惧。",
            motion=(
                "Mysterious humanoid shadow inside black crack, no facial features, dark energy, "
                "Ling Chuan below looking up in fear, slow reveal, cinematic lighting, no text."
            ),
            performance="黑影缓慢显现；凌川仰头闭口；约1.2秒黑影台词，混响，口型不动。",
            ambience="Huge spatial reverb bed, deep resonance",
            camera="低角度仰拍，凌川前景剪影",
            characters_in_shot=["凌川", "神秘黑影"],
            voices=[
                {
                    "role": "神秘黑影",
                    "text": "你终于……回来了。",
                    "start_sec": 1.2,
                    "tts_voice": "onyx",
                    "voice_desc": "极低沉，大空间混响。TTS:onyx",
                    "emotion": "神秘、压迫",
                }
            ],
        ),
        _shot(
            id=9,
            duration=6,
            title="凌川惊醒",
            scene_code="SC-06",
            visual="凌川从噩梦中猛然坐起，冷汗，古代王室卧室内月光透窗，恐惧表情。",
            motion=(
                "Ling Chuan waking up violently in dark royal bedroom, cold sweat, moonlight through window, "
                "frightened expression, SAME face, dramatic close-up, eastern fantasy, no text."
            ),
            performance="惊醒、急促呼吸；无长台词口型。",
            ambience="Heavy breathing, heartbeat, quiet room tone",
            camera="近景，轻微 push in",
            lighting="月光冷蓝，面部半阴影",
            characters_in_shot=["凌川"],
            continuity="从梦境切回现实，同一凌川脸型。",
            voices=[
                {
                    "role": "凌川",
                    "text": "",
                    "note": "喘息与环境",
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo",
                    "emotion": "惊恐",
                }
            ],
        ),
        _shot(
            id=10,
            duration=6,
            title="现实的黑色裂缝",
            scene_code="SC-07",
            visual="凌川推窗望向夜空，真实世界出现巨大黑色裂缝，闪电照云，镜头后拉揭示规模，结尾渐黑。",
            motion=(
                "Ling Chuan at palace window in shock, enormous black crack in real night sky over frozen kingdom, "
                "lightning, snow on palace, slow pull back reveal, epic ending, fade to black hint, no text."
            ),
            performance="约1.0秒起台词口型：「不……它真的来了。」",
            ambience="Massive low rumble, storm, fade to black",
            camera="由中景 slow dolly back 至大全景",
            characters_in_shot=["凌川"],
            voices=[
                {
                    "role": "凌川",
                    "text": "不……它真的来了。",
                    "start_sec": 1.0,
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo 情绪：震惊",
                    "emotion": "震惊",
                }
            ],
        ),
    ]
    return {
        "episode": 1,
        "title": "黑色裂缝",
        "script_md": (
            "《冰境》EP01《黑色裂缝》·约60秒·10镜·东方玄幻史诗。\n"
            "结构：现实王城→梦境预兆→裂缝与黑影→惊醒→现实裂缝降临。\n"
            "制作链：分镜静帧（still_prompt）→ MiniMax H3 图生视频（motion 英文）→ 单镜 TTS/环境声 → 整集剪辑。"
        ),
        "storyboard_md": " ".join(f"{s['id']:02d}{s['title']}" for s in shots),
        "synopsis": (
            "冰雪王国中，王子凌川反复梦见黑色神殿与天际裂缝，以及神秘黑影的低语。"
            "当他在现实中推开窗户，裂缝竟真的出现在王城上空。"
        ),
        "character_bible": (
            "凌川：20多岁东方王子，黑长发，深色王族礼服，写实电影脸，全剧一致。"
            "神秘黑影：裂缝内人形轮廓，无五官。玄清长老/侍卫：第二集起用。"
        ),
        "voice_cast": VOICE_CAST,
        "characters": CHARACTERS,
        "scenes": SCENES_EP1,
        "production": {
            "target_duration_sec": 60,
            "aspect_ratio": "16:9",
            "resolution": "640x360",
            "color_grade": "冷蓝冰雪基调；梦境略去饱和；裂缝场景加青黑与高对比",
            "music_brief": "低频弦乐+冰晶打击乐；裂缝段加入失真合成器铺底",
            "edit_brief": "镜间硬切为主；梦现实切换可用短白闪或风声音桥",
        },
        "audio_design": {
            "dialogue_language": "zh-CN",
            "narrator": "画外 onyx，全程不对口型",
            "ambience_policy": "每镜 ambience 英文关键词供混音检索；环境镜可纯 SFX",
            "reverb": {"神秘黑影": "大空间 hall 混响 40% wet"},
        },
        "style_prefix": STYLE,
        "visual_style": VISUAL_STYLE,
        "audio_sync": AUDIO_SYNC,
        "shots": shots,
    }


def build_ep02() -> Dict[str, Any]:
    shots = [
        _shot(
            id=1,
            duration=6,
            title="裂缝悬城",
            scene_code="SC-01",
            visual="史诗环境定场（无人物、无主角）：黎明前王城上空悬停巨大黑色裂缝，冰晶塔尖前景，城内零星灯火，暴雪转黑雪。",
            motion=(
                "Epic establishing shot, NO people NO hero, black dimensional crack hovering above frozen capital, "
                "ice spires in foreground, few city lights, black snow falling, slow aerial drift, no text."
            ),
            performance="无人物；旁白画外。",
            ambience="Distant alarms, low rumble, black snow hiss",
            camera="航拍大全景，缓慢横移",
            lighting="裂缝下缘暗红溢光，城市冷蓝",
            still_negative=NEG_COMMON + ", 人物特写, 王子",
            voices=[
                {
                    "role": "旁白",
                    "text": "那一夜之后，裂缝没有消失——它像一道伤口，悬在整个王城头顶。",
                    "start_sec": 0.7,
                    "tts_voice": "onyx",
                    "voice_desc": "TTS:onyx 史诗",
                    "emotion": "史诗、压迫",
                }
            ],
        ),
        _shot(
            id=2,
            duration=5,
            title="凌川望天",
            scene_code="SC-07",
            visual="凌川立于昨夜同一高窗，侧脸映裂缝冷光，黑长发，深色礼服，眼下青黑，全片同一脸。",
            motion=(
                "Ling Chuan at palace window, profile lit by crack glow, exhausted eyes, SAME face and costume, "
                "looking up, snow on windowsill, cinematic medium shot, slow push, no text."
            ),
            performance="静止凝视；无对白。",
            ambience="Wind, faint alarm bell",
            characters_in_shot=["凌川"],
            continuity="承接 EP01 末镜同一窗口与裂缝方向。",
            voices=[
                {
                    "role": "凌川",
                    "text": "",
                    "note": "无对白",
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo",
                    "emotion": "疲惫",
                }
            ],
        ),
        _shot(
            id=3,
            duration=5,
            title="急报",
            scene_code="SC-02",
            visual="凌川转身，单膝跪地侍卫入画，冰甲，表情惊慌；长廊深处更多火把移动。",
            motion=(
                "Guard kneeling in ice corridor reporting urgently, Ling Chuan turns, torches moving in depth, "
                "cinematic medium two-shot, realistic, no text."
            ),
            performance="侍卫约0.8秒台词；凌川闭口倾听。",
            ambience="Corridor reverb, armor clink, urgent tone",
            camera="中景双人，轻微 handheld",
            characters_in_shot=["凌川", "侍卫"],
            voices=[
                {
                    "role": "侍卫",
                    "text": "殿下！黑雪落城，祭坛冰纹全裂了！",
                    "start_sec": 0.8,
                    "tts_voice": "echo",
                    "voice_desc": "青年武士，紧张。TTS:echo",
                    "emotion": "紧张",
                }
            ],
        ),
        _shot(
            id=4,
            duration=6,
            title="奔往议事殿",
            scene_code="SC-02",
            visual="凌川披风扬起沿冰廊疾行，跟拍，壁上火把掠过，裂缝光在廊窗一闪。",
            motion=(
                "Tracking shot Ling Chuan running through ice palace corridor, cape flowing, torch lights streak, "
                "crack glow flashes in windows, dynamic but stable camera, SAME costume, no text."
            ),
            performance="奔跑无台词；呼吸加重。",
            ambience="Footsteps, cape, urgent drums low",
            characters_in_shot=["凌川"],
            voices=[
                {
                    "role": "凌川",
                    "text": "",
                    "note": "奔跑戏无对白",
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo",
                    "emotion": "紧迫",
                }
            ],
        ),
        _shot(
            id=5,
            duration=7,
            title="长老警示",
            scene_code="SC-08",
            visual="圆形议事冰殿，玄清长老持杖立于冰台，凌川入殿停步；壁面 ancient 冰纹发光后变暗。",
            motion=(
                "Elder Xuan Qing with ice staff in circular ice council hall, Ling Chuan enters and stops, "
                "ancient patterns on walls dimming, wide cinematic shot, realistic elders, no text."
            ),
            performance="长老约1.0秒起台词；凌川口型静止至后镜。",
            ambience="Hall echo, ice crystal hum, low drone",
            lighting="冰壁内发光纹路，中央顶光",
            characters_in_shot=["凌川", "玄清长老"],
            voices=[
                {
                    "role": "玄清长老",
                    "text": "殿下，古籍称此为天裂。若裂缝闭合，王国将被拖入虚无。",
                    "start_sec": 1.0,
                    "tts_voice": "onyx",
                    "voice_desc": "年迈沙哑。TTS:onyx",
                    "emotion": "忧虑",
                }
            ],
        ),
        _shot(
            id=6,
            duration=6,
            title="黑雪满城",
            scene_code="SC-09",
            visual="王城街道广角，民众仰望，黑雪落在肩与伞上，远处裂缝占天空三分之一，脸小略虚化。",
            motion=(
                "Wide street shot citizens looking up, black snow falling, crack visible in sky, "
                "faces small or soft focus, cinematic crowd, no hero close-up, no text."
            ),
            performance="群众静默仰望；旁白画外。",
            ambience="Crowd murmur, black snow hiss, distant rumble",
            camera="高机位广角",
            voices=[
                {
                    "role": "旁白",
                    "text": "人们第一次看见，雪可以是黑色的。",
                    "start_sec": 0.5,
                    "tts_voice": "onyx",
                    "voice_desc": "TTS:onyx",
                    "emotion": "沉重",
                }
            ],
        ),
        _shot(
            id=7,
            duration=6,
            title="登墙",
            scene_code="SC-10",
            visual="凌川登上城墙垛口，风压黑袍，下方王城与裂缝同框，规模对比。",
            motion=(
                "Ling Chuan reaching battlement, wind pressing robe, city and crack in same frame, "
                "epic wide shot, SAME Ling Chuan, slow pan, no text."
            ),
            performance="约1.2秒凌川台词，口型同步。",
            ambience="High wind on battlement, city noise below",
            characters_in_shot=["凌川"],
            voices=[
                {
                    "role": "凌川",
                    "text": "如果这也是梦……那就让我醒在真相里。",
                    "start_sec": 1.2,
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo 决断",
                    "emotion": "决断",
                }
            ],
        ),
        _shot(
            id=8,
            duration=7,
            title="黑影再现",
            scene_code="SC-10",
            visual="裂缝边缘再次浮现人形黑影，无五官；凌川在下方前景仰头，闪电划过裂缝内壁。",
            motion=(
                "Humanoid shadow reappearing at crack edge, no face, lightning inside crack, "
                "Ling Chuan below looking up, intense atmosphere, slow zoom, no text."
            ),
            performance="黑影约1.5秒台词，大混响；凌川闭口。",
            ambience="Spatial reverb, lightning crack, sub rumble",
            characters_in_shot=["凌川", "神秘黑影"],
            voices=[
                {
                    "role": "神秘黑影",
                    "text": "王城只是门扉。你，才是钥匙。",
                    "start_sec": 1.5,
                    "tts_voice": "onyx",
                    "voice_desc": "混响 onyx",
                    "emotion": "神秘、压迫",
                }
            ],
        ),
        _shot(
            id=9,
            duration=6,
            title="握紧冰符",
            scene_code="SC-10",
            visual="特写：凌川手中握紧一枚 ancient 冰晶符纹，符纹微光与裂缝暗光脉冲同步。",
            motion=(
                "Close-up Ling Chuan hand gripping ancient ice talisman, pulsing glow syncing with crack light, "
                "shallow DOF, realistic skin, no text."
            ),
            performance="约0.9秒凌川低语，口型同步。",
            ambience="Talisman pulse tone, subtle crack resonance",
            voices=[
                {
                    "role": "凌川",
                    "text": "父王留给我的……原来不是护身符。",
                    "start_sec": 0.9,
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo 低语",
                    "emotion": "震惊",
                }
            ],
        ),
        _shot(
            id=10,
            duration=6,
            title="裂缝扩张",
            scene_code="SC-01",
            visual="大全景：裂缝突然向两侧扩张一圈，黑雪暴涌，凌川在墙头渺小；画面渐黑留低频 rumble。",
            motion=(
                "Crack suddenly expands wider, black snow burst, tiny Ling Chuan on wall, "
                "extreme wide apocalyptic shot, slow pull back, end fade to black, no text."
            ),
            performance="凌川震惊后退半步；可选短促无词惊呼口型。",
            ambience="Massive expansion boom, fade to black",
            sfx="扩张冲击波，钟鸣齐响一瞬后断",
            voices=[
                {
                    "role": "凌川",
                    "text": "所有人——退入城内！",
                    "start_sec": 0.6,
                    "tts_voice": "echo",
                    "voice_desc": "TTS:echo 喊声",
                    "emotion": "紧急",
                }
            ],
        ),
    ]
    return {
        "episode": 2,
        "title": "黑雪临城",
        "script_md": (
            "《冰境》EP02《黑雪临城》·约60秒·10镜。\n"
            "承接 EP01 现实裂缝：王城危机、长老示警、黑影再语、凌川发现冰符秘密，裂缝二次扩张。\n"
            "制作要求同 EP01；群像镜保持脸小虚化。"
        ),
        "storyboard_md": " ".join(f"{s['id']:02d}{s['title']}" for s in shots),
        "synopsis": (
            "裂缝悬停王城，黑雪落城。玄清长老警告「天裂」之祸，神秘黑影称凌川为「钥匙」。"
            "凌川意识到父王所留冰符与裂缝同源，裂缝再度扩张，王城陷入紧急戒严。"
        ),
        "character_bible": (
            "凌川/神秘黑影/旁白：与 EP01 完全一致。"
            "玄清长老：灰发深蓝祭袍，60岁写实脸。侍卫：冰甲青年。"
        ),
        "voice_cast": VOICE_CAST,
        "characters": CHARACTERS,
        "scenes": SCENES_EP2,
        "production": {
            "target_duration_sec": 60,
            "aspect_ratio": "16:9",
            "resolution": "640x360",
            "color_grade": "比 EP01 略压暗；黑雪场景降低中间调",
            "music_brief": "延续 EP01 主题，加入紧急鼓点与钟鸣",
            "edit_brief": "与 EP01 末镜可直接硬切；集末 rumble 可做下集声桥",
        },
        "audio_design": {
            "dialogue_language": "zh-CN",
            "narrator": "画外 onyx",
            "crowd": "SC-09 无个体对白，仅环境人声 murmur",
            "reverb": {"神秘黑影": "hall 45% wet"},
        },
        "style_prefix": STYLE,
        "visual_style": VISUAL_STYLE,
        "audio_sync": AUDIO_SYNC,
        "shots": shots,
    }


def main() -> None:
    for builder in (build_ep01, build_ep02):
        ep = builder()
        path = DATA / f"ep{ep['episode']:02d}.json"
        path.write_text(json.dumps(ep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote", path, "shots", len(ep["shots"]))


if __name__ == "__main__":
    main()
