# -*- coding: utf-8 -*-
"""在运行中的冰镜 Web 数据目录创建/刷新「显卡测试｜AI漫剧」项目。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_ROOT))

from app import db, services  # noqa: E402
from app.asset_recovery import save_drama10_export_file  # noqa: E402
from app.drama10_export import episode_to_drama10  # noqa: E402
from app.voice_cast import sync_voice_cast_to_characters  # noqa: E402

PROJECT_ID = "gpu-vram-test-manhua"
PROJECT_NAME = "显卡测试｜AI漫剧压测"
# 分镜图用 720 竖屏保脸；成片用 H3 最低测速 352x640
STILL_W, STILL_H = 720, 1280
VIDEO_W, VIDEO_H = 352, 640
RES_W, RES_H = STILL_W, STILL_H
STYLE = (
    f"海外爆款国漫风格，竖屏9比16，分镜{STILL_W}x{STILL_H}/成片{VIDEO_W}x{VIDEO_H}，"
    "干净线条，柔和办公室光影，人物一致性强，画面无文字无字幕无Logo，单镜头全屏动画"
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
    {
        "role": "男主",
        "tts_voice": "onyx",
        "voice_desc": "虚伪温柔的年轻男性。TTS:onyx",
        "emotion": "冷酷",
        "scope": "episode",
    },
]


def _load(name: str) -> dict:
    path = WEB_ROOT / "data" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_project() -> str:
    try:
        services.get_project(PROJECT_ID)
        services.update_project(
            PROJECT_ID,
            name=PROJECT_NAME,
            description=(
                "显卡专用压测短剧：EP01 十二镜约50秒连贯节奏版；EP02 五镜40秒轻量备选。"
                f"分镜{STILL_W}x{STILL_H}，成片{VIDEO_W}x{VIDEO_H}（测速），适配 MiniMax H3。"
            ),
            episode_count=2,
            width=STILL_W,
            height=STILL_H,
            style_prefix=STYLE,
        )
        return PROJECT_ID
    except KeyError:
        return services.create_project(
            name=PROJECT_NAME,
            description=(
                "显卡专用压测短剧：EP01 十二镜约50秒连贯节奏版；EP02 五镜40秒轻量备选。"
                f"分镜{STILL_W}x{STILL_H}，成片{VIDEO_W}x{VIDEO_H}（测速），适配 MiniMax H3。"
            ),
            episode_count=2,
            width=STILL_W,
            height=STILL_H,
            style_prefix=STYLE,
            project_id=PROJECT_ID,
        )["id"]


def _wipe_characters_and_scenes(pid: str) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM characters WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM scenes WHERE project_id=?", (pid,))


def _apply_shots(eid: str, legacy: dict) -> None:
    shots = []
    for s in legacy.get("shots") or []:
        voices = [v for v in (s.get("voices") or []) if (v.get("text") or "").strip()]
        shots.append(
            {
                "shot_num": int(s.get("id") or s.get("shot_num")),
                "duration_sec": float(s.get("duration_sec", 5)),
                "title": s.get("title") or "",
                "visual": s.get("visual") or "",
                "scene": s.get("scene") or "",
                "motion": s.get("motion") or "",
                "performance": s.get("performance") or "",
                "continuity": s.get("continuity") or "",
                "ambience": s.get("ambience") or s.get("sound") or "",
                "voices": voices,
            }
        )
    # 直接写库，避免 replace_shots → get_episode 合并污染
    with db.connect() as conn:
        conn.execute("DELETE FROM shots WHERE episode_id=?", (eid,))
        for s in shots:
            conn.execute(
                """
                INSERT INTO shots (
                    id, episode_id, shot_num, duration_sec, title, visual, scene, motion,
                    performance, continuity, ambience, voices_json, image_path, video_path
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(__import__("uuid").uuid4()),
                    eid,
                    int(s["shot_num"]),
                    float(s["duration_sec"]),
                    s.get("title", ""),
                    s.get("visual", ""),
                    s.get("scene", ""),
                    s.get("motion", ""),
                    s.get("performance", ""),
                    s.get("continuity", ""),
                    s.get("ambience", ""),
                    json.dumps(s.get("voices") or [], ensure_ascii=False),
                    "",
                    "",
                ),
            )


def _write_clean_drama10(pid: str, eid: str, ep_num: int, legacy: dict) -> str:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM shots WHERE episode_id=? ORDER BY shot_num", (eid,)
        ).fetchall()
        ep = conn.execute("SELECT * FROM episodes WHERE id=?", (eid,)).fetchone()
    payload = {
        "episode": ep_num,
        "title": (ep["title"] if ep else "") or legacy.get("title") or "",
        "style_prefix": STYLE,
        "synopsis": legacy.get("synopsis") or "",
        "character_bible": legacy.get("character_bible") or "",
        "visual_style": legacy.get("visual_style") or STYLE,
        "shots": [],
    }
    for r in rows:
        voices = json.loads(r["voices_json"] or "[]")
        payload["shots"].append(
            {
                "id": int(r["shot_num"]),
                "title": r["title"] or f"镜{r['shot_num']}",
                "duration_sec": float(r["duration_sec"] or 5),
                "style_prefix": STYLE,
                "visual": r["visual"] or "",
                "motion": r["motion"] or "",
                "voices": voices,
                "ambience": r["ambience"] or "",
                "scene": r["scene"] or r["visual"] or "",
                "performance": r["performance"] or "",
                "continuity": r["continuity"] or "",
            }
        )
    prompt = episode_to_drama10(payload, for_video=True)
    save_drama10_export_file(pid, ep_num, prompt)
    with db.connect() as conn:
        conn.execute(
            "UPDATE episodes SET drama10_prompt=? WHERE id=?",
            (prompt, eid),
        )
    return prompt


def _apply_episode(pid: str, legacy: dict, ep_num: int) -> dict:
    eps = services.list_episodes(pid)
    ep_row = next((e for e in eps if int(e["ep_num"]) == int(ep_num)), None)
    if not ep_row:
        raise KeyError(f"EP{ep_num:02d} 不存在")
    eid = ep_row["id"]
    _apply_shots(eid, legacy)
    patch = {"title": legacy.get("title") or ep_row.get("title") or ""}
    if legacy.get("script_md"):
        patch["script_md"] = legacy["script_md"]
    if legacy.get("storyboard_md"):
        patch["storyboard_md"] = legacy["storyboard_md"]
    # 只更新字段，避免经 get_episode 合并
    with db.connect() as conn:
        parts = []
        vals = []
        for k, v in patch.items():
            parts.append(f"{k}=?")
            vals.append(v)
        vals.append(eid)
        conn.execute(f"UPDATE episodes SET {', '.join(parts)} WHERE id=?", vals)
    _write_clean_drama10(pid, eid, ep_num, legacy)
    # 不经 get_episode，避免启动期模块旧逻辑；调用方再验 API
    with db.connect() as conn:
        shots = conn.execute(
            "SELECT shot_num, title, duration_sec, voices_json FROM shots WHERE episode_id=? ORDER BY shot_num",
            (eid,),
        ).fetchall()
    return {
        "id": eid,
        "title": patch.get("title"),
        "shots": [
            {
                "shot_num": r["shot_num"],
                "title": r["title"],
                "duration_sec": r["duration_sec"],
                "voices": json.loads(r["voices_json"] or "[]"),
            }
            for r in shots
        ],
    }


def _install_project_scripts(pid: str, ep1: dict, ep2: dict) -> None:
    """把分集制作 JSON 写入项目目录，供出图/导出只读本剧，不碰共享 epXX.json。"""
    scripts = db.project_dir(pid) / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "ep01.json").write_text(
        json.dumps(ep1, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (scripts / "ep02.json").write_text(
        json.dumps(ep2, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    pid = _ensure_project()
    ep1 = _load("gpu_vram_test_ep01.json")
    ep2 = _load("gpu_vram_test_ep02.json")
    _install_project_scripts(pid, ep1, ep2)

    prefs = {
        "look": "stylized",
        "storyboard_backend": "image_api",
        "storyboard_image_model": "",
        "extra_prefix": "",
        "negative_prompt": (
            "写实照片, 3D渲染糊脸, 文字, 水印, 畸形手指, 多余手指, 肢体扭曲, "
            "photorealistic, subtitle, logo, deformed hands, extra fingers, warped limbs, "
            "面部出画, 头顶裁切, cropped face, cut off head"
        ),
        "global_memory": (
            f"竖屏国漫短剧压测；分镜{STILL_W}x{STILL_H}、成片{VIDEO_W}x{VIDEO_H}；"
            "人物全局一致：苏晚/顾明哲（EP01），男主（EP02轻量）；面部完整入画。"
        ),
        "still_width": STILL_W,
        "still_height": STILL_H,
        "video_width": VIDEO_W,
        "video_height": VIDEO_H,
        "voice_cast": VOICE_CAST,
    }
    services.update_project_docs(
        pid,
        outline_md=(
            "# 显卡测试｜AI漫剧完整单集脚本\n\n"
            "- EP01：辞退反击，12镜约50秒连贯节奏版，镜头10多人涌入为显存压力点\n"
            "- EP02：雨夜录音，5镜40秒，轻量备选（无多人同框）\n"
            f"- 分镜图 {STILL_W}×{STILL_H}；成片 {VIDEO_W}×{VIDEO_H}（测速），MiniMax H3\n"
        ),
        series_scripts_note="压测用途；人物全局复用苏晚/顾明哲（EP01）与轻量男主（EP02）。",
        generation_prefs_json=prefs,
    )

    e1 = _apply_episode(pid, ep1, 1)
    e2 = _apply_episode(pid, ep2, 2)

    # 人物按名字去重写入（避免 voice_cast + ep JSON 各插一遍）
    _wipe_characters_and_scenes(pid)
    merged_chars = {}
    for src in (ep1.get("characters") or []) + (ep2.get("characters") or []):
        name = (src.get("name") or "").strip()
        if not name:
            continue
        prev = merged_chars.get(name) or {}
        if len((src.get("visual_prompt") or "")) >= len((prev.get("visual_prompt") or "")):
            merged_chars[name] = src
    for i, (name, c) in enumerate(merged_chars.items()):
        services.upsert_character(
            pid,
            {
                "name": name,
                "role_note": (c.get("role_note") or "").strip(),
                "visual_prompt": (c.get("visual_prompt") or "").strip()
                or ("无画面形象，仅声音" if name == "旁白" else ""),
                "voice_desc": (c.get("voice_desc") or "").strip(),
                "tts_voice": (c.get("tts_voice") or "").strip(),
                "sort_order": i,
            },
        )
    for sc_src in (ep1.get("scenes") or []) + (ep2.get("scenes") or []):
        services.upsert_scene(
            pid,
            {
                "code": (sc_src.get("code") or "").strip(),
                "name": (sc_src.get("name") or "").strip(),
                "visual_prompt": (sc_src.get("visual_prompt") or "").strip(),
                "sort_order": int(sc_src.get("sort_order") or 0),
            },
        )
    sync_voice_cast_to_characters(pid, VOICE_CAST, episode_id="")

    print(
        json.dumps(
            {
                "project_id": pid,
                "name": PROJECT_NAME,
                "width": STILL_W,
                "height": STILL_H,
                "still": f"{STILL_W}x{STILL_H}",
                "video": f"{VIDEO_W}x{VIDEO_H}",
                "chars": [c.get("name") for c in services.list_characters(pid)],
                "ep1": {
                    "id": e1["id"],
                    "title": e1.get("title"),
                    "shots": len(e1.get("shots") or []),
                    "voices_ok": all(
                        "卡索" not in json.dumps(s.get("voices") or [], ensure_ascii=False)
                        for s in (e1.get("shots") or [])
                    ),
                },
                "ep2": {
                    "id": e2["id"],
                    "title": e2.get("title"),
                    "shots": len(e2.get("shots") or []),
                    "shot4_voices": (e2.get("shots") or [{}])[3].get("voices")
                    if len(e2.get("shots") or []) >= 4
                    else None,
                    "voices_ok": all(
                        "卡索" not in json.dumps(s.get("voices") or [], ensure_ascii=False)
                        and "凌川" not in json.dumps(s.get("voices") or [], ensure_ascii=False)
                        for s in (e2.get("shots") or [])
                    ),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
