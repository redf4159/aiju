# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db
from .comfy_client import (
    COMFY_QUEUE_LOCK,
    ComfyClient,
    copy_into_project,
    find_newest_under,
    finalize_storyboard_tile,
    fix_portrait_orientation,
    reprocess_storyboard_episode,
    manual_rotate_portrait_shot,
    pick_first_saved_still,
    resolve_output_files,
    storyboard_tile_rotate_degrees,
    comfy_root,
)
from .drama10_export import (
    adapt_canvas_for_video,
    compact_shot_record,
    compute_grid,
    drama10_params,
    episode_to_drama10,
    filter_prompt_shots,
    merge_shots_from_drama10_prompt,
    still_prompt_for_shot,
)
from .generation_prefs import (
    apply_negative_to_visual,
    default_prefs,
    effective_style,
    resolve_still_canvas,
    resolve_video_source_canvas,
    parse_prefs,
    style_prefix_for_shot,
    _is_black_shot,
    _is_establishing_shot,
    _needs_face_hint,
    validate_still_not_near_black,
)
from .drama10_workflows import build_episode_video_workflow
from .storyboard_image_api import (
    BACKEND_IMAGE_API,
    generate_still_via_api_temp,
    image_api_configured,
    image_api_model,
)
from .storyboard_still_workflows import (
    BACKEND_MINIMAX,
    BACKEND_SDXL,
    build_single_shot_still_workflow,
    list_storyboard_backends,
    resolve_storyboard_backend,
)


def list_storyboard_backend_options() -> List[Dict[str, str]]:
    return list_storyboard_backends()


def storyboard_image_api_ready() -> bool:
    from .studio_settings import ohmygpt_configured

    return ohmygpt_configured()


def get_ohmygpt_settings() -> Dict[str, Any]:
    from .studio_settings import get_ohmygpt_settings_public

    return get_ohmygpt_settings_public()


def save_ohmygpt_settings(
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    clear_key: bool = False,
) -> Dict[str, Any]:
    from .studio_settings import update_ohmygpt_settings

    return update_ohmygpt_settings(
        api_base=api_base, api_key=api_key, clear_key=clear_key
    )


def get_video_backend_settings() -> Dict[str, Any]:
    from .studio_settings import get_video_backend_settings_public

    return get_video_backend_settings_public()


def save_video_backend_settings(
    *,
    backend: Optional[str] = None,
    ppio_api_key: Optional[str] = None,
    ppio_endpoint_key: Optional[str] = None,
    ppio_async_base: Optional[str] = None,
    clear_ppio_key: bool = False,
    minimax_api_key: Optional[str] = None,
    minimax_api_base: Optional[str] = None,
    minimax_model: Optional[str] = None,
    minimax_resolution: Optional[str] = None,
    clear_minimax_key: bool = False,
) -> Dict[str, Any]:
    from .studio_settings import update_video_backend_settings

    return update_video_backend_settings(
        backend=backend,
        ppio_api_key=ppio_api_key,
        ppio_endpoint_key=ppio_endpoint_key,
        ppio_async_base=ppio_async_base,
        clear_ppio_key=clear_ppio_key,
        minimax_api_key=minimax_api_key,
        minimax_api_base=minimax_api_base,
        minimax_model=minimax_model,
        minimax_resolution=minimax_resolution,
        clear_minimax_key=clear_minimax_key,
    )


def list_script_llm_model_options() -> List[Dict[str, str]]:
    from .drama10_script_llm import list_script_model_options

    return list_script_model_options()


from .pipeline_stages import EPISODE_STAGES, PROJECT_STAGES
from .asset_recovery import (
    CANONICAL_BINGJING_PROJECT_ID,
    ensure_episode_assets,
    read_drama10_export_file,
    recover_production_data_on_startup,
    save_drama10_export_file,
)


def list_projects() -> List[Dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*, d.script_meta_json, d.novel_md
            FROM projects p
            LEFT JOIN project_docs d ON d.project_id = p.id
            ORDER BY p.updated_at DESC
            """
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        raw = item.pop("script_meta_json", None) or "{}"
        novel = item.pop("novel_md", None) or ""
        try:
            meta = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            meta = {}
        item["docs"] = {"script_meta": meta, "script_meta_json": raw, "novel_md": novel}
        out.append(item)
    return out


def delete_project(project_id: str) -> None:
    """删除短剧项目及其数据库行、磁盘资产。不允许误删时通过 API 恢复。"""
    import shutil

    from .asset_recovery import is_bingjing_project_id

    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("项目 ID 为空")
    proj = get_project(pid)
    if is_bingjing_project_id(pid) or (proj.get("name") or "").strip() == "冰镜":
        raise ValueError("系统种子项目「冰镜」不可删除")
    with db.connect() as conn:
        ep_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM episodes WHERE project_id=?", (pid,)
            ).fetchall()
        ]
        for eid in ep_ids:
            conn.execute("DELETE FROM shots WHERE episode_id=?", (eid,))
        conn.execute("DELETE FROM episodes WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM characters WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM scenes WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM props WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM jobs WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM project_docs WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    pdir = db.DATA_ROOT / "projects" / pid
    if pdir.is_dir():
        shutil.rmtree(pdir, ignore_errors=True)


def clear_project_media(project_id: str) -> Dict[str, Any]:
    """清除项目下分镜图/视频路径与磁盘媒体（剧本与人物库保留）。"""
    import shutil

    pid = (project_id or "").strip()
    get_project(pid)
    removed = {"images": 0, "videos": 0, "dirs": 0}
    with db.connect() as conn:
        eps = conn.execute(
            "SELECT id, ep_num FROM episodes WHERE project_id=?", (pid,)
        ).fetchall()
        for ep in eps:
            conn.execute(
                "UPDATE shots SET image_path='', video_path='' WHERE episode_id=?",
                (ep["id"],),
            )
            conn.execute(
                "UPDATE episodes SET video_path='' WHERE id=?",
                (ep["id"],),
            )
    base = db.project_dir(pid)
    for sub in ("episodes",):
        root = base / sub
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}:
                try:
                    p.unlink()
                    if p.suffix.lower() == ".mp4" or p.suffix.lower() == ".webm":
                        removed["videos"] += 1
                    else:
                        removed["images"] += 1
                except OSError:
                    pass
    # 角色参考图也清掉（避免串项目残留）
    char_root = base / "characters"
    if char_root.is_dir():
        shutil.rmtree(char_root, ignore_errors=True)
        removed["dirs"] += 1
        char_root.mkdir(parents=True, exist_ok=True)
    return removed


def create_project(
    name: str,
    description: str = "",
    episode_count: int = 20,
    width: int = 640,
    height: int = 360,
    style_prefix: str = "",
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    pid = (project_id or str(uuid.uuid4())).strip()
    try:
        get_project(pid)
        raise ValueError(f"项目 ID 已存在: {pid}")
    except KeyError:
        pass
    now = db._utcnow()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO projects (id,name,description,episode_count,width,height,style_prefix,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (pid, name, description, episode_count, width, height, style_prefix, now, now),
        )
        conn.execute(
            """
            INSERT INTO project_docs (project_id, pipeline_json, generation_prefs_json)
            VALUES (?,?,?)
            """,
            (
                pid,
                json.dumps(_default_project_pipeline(), ensure_ascii=False),
                json.dumps(default_prefs(), ensure_ascii=False),
            ),
        )
        for n in range(1, episode_count + 1):
            eid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO episodes (id, project_id, ep_num, title, pipeline_json)
                VALUES (?,?,?,?,?)
                """,
                (
                    eid,
                    pid,
                    n,
                    f"第{n}集",
                    json.dumps(_default_episode_pipeline(), ensure_ascii=False),
                ),
            )
    db.project_dir(pid)
    return get_project(pid)


def get_project(project_id: str) -> Dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if not row:
            raise KeyError("project not found")
        doc = conn.execute(
            "SELECT * FROM project_docs WHERE project_id=?", (project_id,)
        ).fetchone()
    out = dict(row)
    docs = dict(doc) if doc else {}
    raw_meta = docs.get("script_meta_json") or "{}"
    if isinstance(raw_meta, str):
        try:
            docs["script_meta"] = json.loads(raw_meta or "{}")
        except json.JSONDecodeError:
            docs["script_meta"] = {}
    elif isinstance(raw_meta, dict):
        docs["script_meta"] = raw_meta
    else:
        docs["script_meta"] = {}
    out["docs"] = docs
    return out


def update_project(project_id: str, **fields: Any) -> Dict[str, Any]:
    allowed = {
        "name",
        "description",
        "episode_count",
        "width",
        "height",
        "style_prefix",
    }
    parts = []
    vals: List[Any] = []
    for k, v in fields.items():
        if k in allowed and v is not None:
            parts.append(f"{k}=?")
            vals.append(v)
    if parts:
        parts.append("updated_at=?")
        vals.append(db._utcnow())
        vals.append(project_id)
        with db.connect() as conn:
            conn.execute(
                f"UPDATE projects SET {', '.join(parts)} WHERE id=?", vals
            )
    return get_project(project_id)


def get_generation_prefs(project_id: str) -> Dict[str, Any]:
    proj = get_project(project_id)
    docs = proj.get("docs") or {}
    return parse_prefs(docs.get("generation_prefs_json") or "{}")


def update_project_docs(project_id: str, **fields: Any) -> Dict[str, Any]:
    allowed = (
        "outline_md",
        "series_scripts_note",
        "pipeline_json",
        "generation_prefs_json",
        "novel_md",
        "script_meta_json",
    )
    parts = []
    vals: List[Any] = []
    for k, v in fields.items():
        if k in allowed and v is not None:
            parts.append(f"{k}=?")
            if k in ("pipeline_json", "generation_prefs_json", "script_meta_json"):
                vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
            else:
                vals.append(v)
    if parts:
        vals.append(project_id)
        with db.connect() as conn:
            conn.execute(
                f"UPDATE project_docs SET {', '.join(parts)} WHERE project_id=?",
                vals,
            )
    return get_project(project_id)


def _json_list(raw: Any) -> List[Any]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []


def _normalize_character(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    voices = _json_list(out.get("voice_samples_json"))
    images = _json_list(out.get("ref_images_json"))
    legacy = (out.get("ref_image") or "").strip()
    if legacy and not any(
        (isinstance(x, dict) and x.get("path") == legacy) or x == legacy for x in images
    ):
        images.insert(0, {"path": legacy, "label": "参考"})
    out["voice_samples"] = [str(p) for p in voices if p]
    out["ref_images"] = []
    for item in images:
        if isinstance(item, dict) and item.get("path"):
            out["ref_images"].append(
                {"path": str(item["path"]), "label": str(item.get("label") or "形象")}
            )
        elif isinstance(item, str) and item.strip():
            out["ref_images"].append({"path": item.strip(), "label": "形象"})
    eps = _json_list(out.get("episodes_json"))
    out["episodes"] = sorted(
        {int(x) for x in eps if str(x).isdigit() or isinstance(x, int)}
    )
    return out


def _normalize_prop(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    images = _json_list(out.get("ref_images_json"))
    legacy = (out.get("ref_image") or "").strip()
    if legacy and not any(
        (isinstance(x, dict) and x.get("path") == legacy) or x == legacy for x in images
    ):
        images.insert(0, {"path": legacy, "label": "参考"})
    out["ref_images"] = []
    for item in images:
        if isinstance(item, dict) and item.get("path"):
            out["ref_images"].append(
                {"path": str(item["path"]), "label": str(item.get("label") or "形象")}
            )
        elif isinstance(item, str) and item.strip():
            out["ref_images"].append({"path": item.strip(), "label": "形象"})
    eps = _json_list(out.get("episodes_json"))
    out["episodes"] = sorted(
        {int(x) for x in eps if str(x).isdigit() or isinstance(x, int)}
    )
    return out


def list_characters(project_id: str, episode_id: Optional[str] = None) -> List[Dict]:
    q = "SELECT * FROM characters WHERE project_id=?"
    params: List[Any] = [project_id]
    if episode_id:
        q += " AND episode_id=?"
        params.append(episode_id)
    else:
        q += " AND (episode_id IS NULL OR episode_id='')"
    q += " ORDER BY sort_order, name"
    with db.connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return [_normalize_character(dict(r)) for r in rows]


def get_character(project_id: str, character_id: str) -> Dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE id=? AND project_id=?",
            (character_id, project_id),
        ).fetchone()
    if not row:
        raise KeyError("character not found")
    return _normalize_character(dict(row))


def upsert_character(project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    cid = data.get("id") or str(uuid.uuid4())
    voice_samples = data.get("voice_samples_json")
    if voice_samples is None and "voice_samples" in data:
        voice_samples = json.dumps(data.get("voice_samples") or [], ensure_ascii=False)
    elif isinstance(voice_samples, list):
        voice_samples = json.dumps(voice_samples, ensure_ascii=False)
    elif not voice_samples:
        voice_samples = "[]"
    ref_images = data.get("ref_images_json")
    if ref_images is None and "ref_images" in data:
        ref_images = json.dumps(data.get("ref_images") or [], ensure_ascii=False)
    elif isinstance(ref_images, list):
        ref_images = json.dumps(ref_images, ensure_ascii=False)
    elif not ref_images:
        ref_images = "[]"
    ref_image = data.get("ref_image", "")
    try:
        imgs = json.loads(ref_images) if isinstance(ref_images, str) else ref_images
        if imgs and isinstance(imgs[0], dict) and imgs[0].get("path"):
            ref_image = ref_image or imgs[0]["path"]
    except (json.JSONDecodeError, TypeError, IndexError):
        pass
    episodes_json = data.get("episodes_json")
    if episodes_json is None and "episodes" in data:
        episodes_json = json.dumps(data.get("episodes") or [], ensure_ascii=False)
    elif isinstance(episodes_json, list):
        episodes_json = json.dumps(episodes_json, ensure_ascii=False)
    elif not episodes_json:
        episodes_json = "[]"
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO characters (
                id, project_id, episode_id, name, role_note, visual_prompt, ref_image,
                sort_order, voice_desc, voice_samples_json, ref_images_json, tts_voice,
                episodes_json
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name, role_note=excluded.role_note, visual_prompt=excluded.visual_prompt,
              ref_image=excluded.ref_image, episode_id=excluded.episode_id, sort_order=excluded.sort_order,
              voice_desc=excluded.voice_desc, voice_samples_json=excluded.voice_samples_json,
              ref_images_json=excluded.ref_images_json, tts_voice=excluded.tts_voice,
              episodes_json=excluded.episodes_json
            """,
            (
                cid,
                project_id,
                data.get("episode_id") or "",
                data["name"],
                data.get("role_note", ""),
                data.get("visual_prompt", ""),
                ref_image,
                int(data.get("sort_order", 0)),
                data.get("voice_desc", "") or "",
                voice_samples,
                ref_images,
                (data.get("tts_voice") or "").strip().lower(),
                episodes_json,
            ),
        )
        row = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
    return _normalize_character(dict(row))


def append_character_voice_sample(
    project_id: str, character_id: str, rel_path: str
) -> Dict[str, Any]:
    ch = get_character(project_id, character_id)
    paths = list(ch.get("voice_samples") or [])
    if rel_path not in paths:
        paths.append(rel_path)
    return upsert_character(
        project_id,
        {
            "id": character_id,
            "name": ch["name"],
            "role_note": ch.get("role_note") or "",
            "visual_prompt": ch.get("visual_prompt") or "",
            "ref_image": ch.get("ref_image") or "",
            "voice_desc": ch.get("voice_desc") or "",
            "voice_samples": paths,
            "ref_images": ch.get("ref_images") or [],
            "sort_order": int(ch.get("sort_order") or 0),
            "episode_id": ch.get("episode_id") or "",
        },
    )


def generate_character_image_via_ohmygpt(
    project_id: str,
    character_id: str,
    *,
    angle_label: str = "正面",
    extra: str = "",
) -> Dict[str, Any]:
    from .character_ohmygpt import generate_character_portrait_file

    if not storyboard_image_api_ready():
        raise RuntimeError("请先在「制作设置」保存 OhMyGPT API Key")
    ch = get_character(project_id, character_id)
    proj = get_project(project_id)
    prefs = get_generation_prefs(project_id)
    w, h = resolve_still_canvas(proj, prefs)
    dest_dir = db.project_dir(project_id) / "characters" / character_id / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    label = (angle_label or "正面").strip() or "正面"
    dest = dest_dir / f"ai_{label}_{uuid.uuid4().hex[:8]}.png"
    generate_character_portrait_file(
        dest,
        name=ch["name"],
        visual_prompt=ch.get("visual_prompt") or "",
        role_note=ch.get("role_note") or "",
        style_prefix=proj.get("style_prefix") or "",
        prefs=prefs,
        width=w,
        height=h,
        angle_label=label,
        extra=extra or "",
        kind="character",
    )
    rel = str(dest.relative_to(db.project_dir(project_id))).replace("\\", "/")
    return append_character_ref_image(project_id, character_id, rel, label=f"AI·{label}")


def generate_scene_image_via_ohmygpt(
    project_id: str,
    scene_id: str,
    *,
    extra: str = "",
) -> Dict[str, Any]:
    from .character_ohmygpt import generate_character_portrait_file

    if not storyboard_image_api_ready():
        raise RuntimeError("请先在「制作设置」保存 OhMyGPT API Key")
    sc = None
    for s in list_scenes(project_id):
        if s.get("id") == scene_id:
            sc = s
            break
    if not sc:
        raise KeyError("scene not found")
    proj = get_project(project_id)
    prefs = get_generation_prefs(project_id)
    w, h = resolve_still_canvas(proj, prefs)
    dest_dir = db.project_dir(project_id) / "scenes" / scene_id / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"ai_{uuid.uuid4().hex[:8]}.png"
    name = sc.get("name") or sc.get("code") or "场景"
    generate_character_portrait_file(
        dest,
        name=name,
        visual_prompt=sc.get("visual_prompt") or name,
        role_note=sc.get("role_note") or "场景空镜母景，无主要角色面部特写",
        style_prefix=proj.get("style_prefix") or "",
        prefs=prefs,
        width=w,
        height=h,
        angle_label="场景母景",
        extra=extra or "",
        kind="scene",
    )
    rel = str(dest.relative_to(db.project_dir(project_id))).replace("\\", "/")
    imgs = list(sc.get("ref_images") or [])
    imgs.append({"path": rel, "label": "AI·场景"})
    return upsert_scene(
        project_id,
        {
            "id": scene_id,
            "name": name,
            "code": sc.get("code") or "",
            "visual_prompt": sc.get("visual_prompt") or "",
            "ref_image": rel,
            "ref_images": imgs,
            "episodes": sc.get("episodes") or [],
            "sort_order": int(sc.get("sort_order") or 0),
            "episode_id": sc.get("episode_id") or "",
        },
    )


def generate_prop_image_via_ohmygpt(
    project_id: str,
    prop_id: str,
    *,
    extra: str = "",
) -> Dict[str, Any]:
    from .character_ohmygpt import generate_character_portrait_file

    if not storyboard_image_api_ready():
        raise RuntimeError("请先在「制作设置」保存 OhMyGPT API Key")
    prop = get_prop(project_id, prop_id)
    proj = get_project(project_id)
    prefs = get_generation_prefs(project_id)
    w, h = resolve_still_canvas(proj, prefs)
    dest_dir = db.project_dir(project_id) / "props" / prop_id / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"ai_{uuid.uuid4().hex[:8]}.png"
    name = prop.get("name") or "道具"
    generate_character_portrait_file(
        dest,
        name=name,
        visual_prompt=prop.get("visual_prompt") or name,
        role_note=prop.get("role_note") or "道具特写，干净背景",
        style_prefix=proj.get("style_prefix") or "",
        prefs=prefs,
        width=w,
        height=h,
        angle_label="道具设定",
        extra=extra or "",
        kind="prop",
    )
    rel = str(dest.relative_to(db.project_dir(project_id))).replace("\\", "/")
    return append_prop_ref_image(project_id, prop_id, rel, label="AI·道具")


def batch_generate_asset_images(
    project_id: str,
    *,
    kind: str = "characters",
    only_missing: bool = True,
    limit: int = 12,
    ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """批量生成角色/场景/道具参考图（生成块图）。ids 指定时只处理这些资产，并逐个生成。"""
    if not storyboard_image_api_ready():
        raise RuntimeError("请先在「制作设置」保存 OhMyGPT API Key")
    kind = (kind or "characters").strip()
    id_set = {str(x).strip() for x in (ids or []) if str(x).strip()}
    limit = max(1, min(40, int(limit or 12)))
    if id_set:
        limit = max(limit, len(id_set))
        limit = min(40, limit)
    done: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    if kind == "characters":
        items = list_characters(project_id)
        for ch in items:
            if id_set and ch["id"] not in id_set:
                continue
            if only_missing and (ch.get("ref_images") or []):
                continue
            if len(done) + len(errors) >= limit:
                break
            try:
                done.append(
                    {
                        "id": ch["id"],
                        "name": ch.get("name"),
                        "result": generate_character_image_via_ohmygpt(
                            project_id, ch["id"]
                        ),
                    }
                )
            except Exception as e:
                errors.append({"id": ch["id"], "name": ch.get("name") or "", "error": str(e)[:200]})
    elif kind == "scenes":
        items = list_scenes(project_id)
        for sc in items:
            if id_set and sc["id"] not in id_set:
                continue
            if only_missing and (sc.get("ref_images") or sc.get("ref_image")):
                continue
            if len(done) + len(errors) >= limit:
                break
            try:
                done.append(
                    {
                        "id": sc["id"],
                        "name": sc.get("name"),
                        "result": generate_scene_image_via_ohmygpt(project_id, sc["id"]),
                    }
                )
            except Exception as e:
                errors.append({"id": sc["id"], "name": sc.get("name") or "", "error": str(e)[:200]})
    elif kind == "props":
        items = list_props(project_id)
        for p in items:
            if id_set and p["id"] not in id_set:
                continue
            if only_missing and (p.get("ref_images") or []):
                continue
            if len(done) + len(errors) >= limit:
                break
            try:
                done.append(
                    {
                        "id": p["id"],
                        "name": p.get("name"),
                        "result": generate_prop_image_via_ohmygpt(project_id, p["id"]),
                    }
                )
            except Exception as e:
                errors.append({"id": p["id"], "name": p.get("name") or "", "error": str(e)[:200]})
    else:
        raise ValueError("kind 须为 characters / scenes / props")
    return {
        "kind": kind,
        "generated": len(done),
        "failed": len(errors),
        "items": done,
        "errors": errors,
    }


def generate_character_voice_via_ohmygpt(
    project_id: str,
    character_id: str,
    *,
    demo_text: Optional[str] = None,
    voice: Optional[str] = None,
) -> Dict[str, Any]:
    from .character_ohmygpt import generate_character_voice_file

    if not storyboard_image_api_ready():
        raise RuntimeError("请先在「全局制作」保存 OhMyGPT API Key")
    ch = get_character(project_id, character_id)
    name = ch.get("name") or "角色"
    text = (demo_text or "").strip()
    if not text:
        vd = (ch.get("voice_desc") or "").strip()
        text = vd if len(vd) >= 4 else f"你好，我是{name}。这是一段音色试听。"
    dest_dir = db.project_dir(project_id) / "characters" / character_id / "voices"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = (os.environ.get("CHAR_TTS_FORMAT") or "mp3").strip().lower()
    if ext not in ("mp3", "wav", "opus", "aac", "flac"):
        ext = "mp3"
    dest = dest_dir / f"ai_{uuid.uuid4().hex[:10]}.{ext}"
    generate_character_voice_file(dest, text=text, voice=voice)
    rel = str(dest.relative_to(db.project_dir(project_id))).replace("\\", "/")
    return append_character_voice_sample(project_id, character_id, rel)


def append_character_ref_image(
    project_id: str, character_id: str, rel_path: str, label: str = "形象"
) -> Dict[str, Any]:
    ch = get_character(project_id, character_id)
    images = list(ch.get("ref_images") or [])
    images.append({"path": rel_path, "label": label or "形象"})
    return upsert_character(
        project_id,
        {
            "id": character_id,
            "name": ch["name"],
            "role_note": ch.get("role_note") or "",
            "visual_prompt": ch.get("visual_prompt") or "",
            "ref_image": rel_path,
            "voice_desc": ch.get("voice_desc") or "",
            "voice_samples": ch.get("voice_samples") or [],
            "ref_images": images,
            "sort_order": int(ch.get("sort_order") or 0),
            "episode_id": ch.get("episode_id") or "",
        },
    )


def list_scenes(project_id: str, episode_id: Optional[str] = None) -> List[Dict]:
    q = "SELECT * FROM scenes WHERE project_id=?"
    params: List[Any] = [project_id]
    if episode_id:
        q += " AND episode_id=?"
        params.append(episode_id)
    else:
        q += " AND (episode_id IS NULL OR episode_id='')"
    q += " ORDER BY sort_order, name"
    with db.connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def upsert_scene(project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    sid = data.get("id") or str(uuid.uuid4())
    episodes_json = data.get("episodes_json")
    if episodes_json is None and "episodes" in data:
        episodes_json = json.dumps(data.get("episodes") or [], ensure_ascii=False)
    elif isinstance(episodes_json, list):
        episodes_json = json.dumps(episodes_json, ensure_ascii=False)
    elif not episodes_json:
        episodes_json = "[]"
    ref_images = data.get("ref_images_json")
    if ref_images is None and "ref_images" in data:
        ref_images = json.dumps(data.get("ref_images") or [], ensure_ascii=False)
    elif isinstance(ref_images, list):
        ref_images = json.dumps(ref_images, ensure_ascii=False)
    elif not ref_images:
        ref_images = "[]"
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO scenes (
                id, project_id, episode_id, code, name, visual_prompt, ref_image,
                sort_order, episodes_json, ref_images_json, role_note
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              code=excluded.code, name=excluded.name, visual_prompt=excluded.visual_prompt,
              ref_image=excluded.ref_image, episode_id=excluded.episode_id, sort_order=excluded.sort_order,
              episodes_json=excluded.episodes_json, ref_images_json=excluded.ref_images_json,
              role_note=excluded.role_note
            """,
            (
                sid,
                project_id,
                data.get("episode_id") or "",
                data.get("code", ""),
                data["name"],
                data.get("visual_prompt", ""),
                data.get("ref_image", ""),
                int(data.get("sort_order", 0)),
                episodes_json,
                ref_images,
                data.get("role_note", "") or "",
            ),
        )
        row = conn.execute("SELECT * FROM scenes WHERE id=?", (sid,)).fetchone()
    out = dict(row)
    out["episodes"] = _json_list(out.get("episodes_json"))
    out["ref_images"] = _json_list(out.get("ref_images_json"))
    return out


def list_props(project_id: str, episode_id: Optional[str] = None) -> List[Dict]:
    q = "SELECT * FROM props WHERE project_id=?"
    params: List[Any] = [project_id]
    if episode_id:
        q += " AND episode_id=?"
        params.append(episode_id)
    else:
        q += " AND (episode_id IS NULL OR episode_id='')"
    q += " ORDER BY sort_order, name"
    with db.connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return [_normalize_prop(dict(r)) for r in rows]


def get_prop(project_id: str, prop_id: str) -> Dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM props WHERE id=? AND project_id=?",
            (prop_id, project_id),
        ).fetchone()
    if not row:
        raise KeyError("prop not found")
    return _normalize_prop(dict(row))


def upsert_prop(project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    pid = data.get("id") or str(uuid.uuid4())
    episodes_json = data.get("episodes_json")
    if episodes_json is None and "episodes" in data:
        episodes_json = json.dumps(data.get("episodes") or [], ensure_ascii=False)
    elif isinstance(episodes_json, list):
        episodes_json = json.dumps(episodes_json, ensure_ascii=False)
    elif not episodes_json:
        episodes_json = "[]"
    ref_images = data.get("ref_images_json")
    if ref_images is None and "ref_images" in data:
        ref_images = json.dumps(data.get("ref_images") or [], ensure_ascii=False)
    elif isinstance(ref_images, list):
        ref_images = json.dumps(ref_images, ensure_ascii=False)
    elif not ref_images:
        ref_images = "[]"
    ref_image = data.get("ref_image", "")
    try:
        imgs = json.loads(ref_images) if isinstance(ref_images, str) else ref_images
        if imgs and isinstance(imgs[0], dict) and imgs[0].get("path"):
            ref_image = ref_image or imgs[0]["path"]
    except (json.JSONDecodeError, TypeError, IndexError):
        pass
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO props (
                id, project_id, episode_id, name, role_note, visual_prompt, ref_image,
                ref_images_json, episodes_json, sort_order
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name, role_note=excluded.role_note, visual_prompt=excluded.visual_prompt,
              ref_image=excluded.ref_image, episode_id=excluded.episode_id, sort_order=excluded.sort_order,
              ref_images_json=excluded.ref_images_json, episodes_json=excluded.episodes_json
            """,
            (
                pid,
                project_id,
                data.get("episode_id") or "",
                data["name"],
                data.get("role_note", ""),
                data.get("visual_prompt", ""),
                ref_image,
                ref_images,
                episodes_json,
                int(data.get("sort_order", 0)),
            ),
        )
        row = conn.execute("SELECT * FROM props WHERE id=?", (pid,)).fetchone()
    return _normalize_prop(dict(row))


def append_prop_ref_image(
    project_id: str, prop_id: str, rel_path: str, label: str = "形象"
) -> Dict[str, Any]:
    prop = get_prop(project_id, prop_id)
    images = list(prop.get("ref_images") or [])
    images.append({"path": rel_path, "label": label or "形象"})
    return upsert_prop(
        project_id,
        {
            "id": prop_id,
            "name": prop["name"],
            "role_note": prop.get("role_note") or "",
            "visual_prompt": prop.get("visual_prompt") or "",
            "ref_image": rel_path,
            "ref_images": images,
            "episodes": prop.get("episodes") or [],
            "sort_order": int(prop.get("sort_order") or 0),
            "episode_id": prop.get("episode_id") or "",
        },
    )


def list_episodes(project_id: str) -> List[Dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM episodes WHERE project_id=? ORDER BY ep_num",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _mp4_has_audio_stream(path: Path) -> bool:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(path),
            ],
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        return b"audio" in out.lower()
    except (subprocess.SubprocessError, OSError, ValueError):
        return False


def _mp4_audio_is_hum_only(path: Path) -> bool:
    """检测「只有电流/平坦底噪、没有对白起伏」的坏音轨（有 aac 但仍不可用）。

    对白段音量会随时间明显起伏；电流声则多段窗口响度几乎不变。
    """
    path = Path(path)
    if not path.is_file() or not _mp4_has_audio_stream(path):
        return False
    try:
        dur_s = float(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(path),
                ],
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            .decode("utf-8", "replace")
            .strip()
            or "0"
        )
    except (subprocess.SubprocessError, OSError, ValueError):
        return False
    if dur_s < 2.0:
        return False
    means: List[float] = []
    for frac in (0.12, 0.38, 0.62, 0.85):
        ss = max(0.0, min(dur_s - 1.4, dur_s * frac))
        try:
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-ss",
                    f"{ss:.2f}",
                    "-t",
                    "1.3",
                    "-i",
                    str(path),
                    "-af",
                    "volumedetect",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=40,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        for line in (proc.stderr or "").splitlines():
            if "mean_volume:" in line:
                try:
                    means.append(
                        float(line.split("mean_volume:")[1].split("dB")[0].strip())
                    )
                except (IndexError, ValueError):
                    pass
                break
    if len(means) < 3:
        return False
    spread = max(means) - min(means)
    avg = sum(means) / len(means)
    # 多段响度几乎一样，且处于「听得见的嗡鸣」区间 → 判为电流声坏轨
    return spread < 2.5 and -40.0 <= avg <= -12.0


def _estimate_line_speech_sec(text: str) -> float:
    """估算中文对白时长（偏保守，避免句尾被镜长截断）。"""
    t = (text or "").strip()
    if not t:
        return 0.0
    pause = 0.12 * (t.count("，") + t.count("、") + t.count("；") + t.count(","))
    pause += 0.28 * (
        t.count("。") + t.count("！") + t.count("？") + t.count("…") + t.count("?") + t.count("!")
    )
    # ~3 字/秒 + 气口；最短约 1.6s
    return max(1.6, len(t) / 3.0 + pause + 0.4)


def _min_duration_covering_voices(
    voices: Any, current_dur: float, *, tail_sec: float = 1.25
) -> float:
    last_end = 0.0
    if isinstance(voices, list):
        for v in voices:
            if not isinstance(v, dict):
                continue
            text = (v.get("text") or "").strip()
            if len(text) < 1:
                continue
            try:
                st = float(v.get("start_sec") or 0.8)
            except (TypeError, ValueError):
                st = 0.8
            last_end = max(last_end, st + _estimate_line_speech_sec(text))
    cur = max(2.0, float(current_dur or 5.0))
    if last_end <= 0:
        return cur
    need = last_end + float(tail_sec)
    if need <= cur + 0.05:
        return cur
    # 向上取整到 0.5 秒，最长 30s
    import math

    return min(30.0, math.ceil(need * 2.0) / 2.0)


def ensure_shot_durations_cover_dialogue(
    episode_id: str, shot_nums: Optional[List[int]] = None
) -> List[Dict[str, Any]]:
    """成片前拉长镜长，保证末句说完；写回 DB。返回调整明细。"""
    ep = get_episode(episode_id)
    want = {int(x) for x in shot_nums} if shot_nums else None
    changed: List[Dict[str, Any]] = []
    for s in ep.get("shots") or []:
        sn = int(s["shot_num"])
        if want is not None and sn not in want:
            continue
        voices = s.get("voices")
        if voices is None:
            try:
                voices = json.loads(s.get("voices_json") or "[]")
            except json.JSONDecodeError:
                voices = []
        old = float(s.get("duration_sec") or 5.0)
        new = _min_duration_covering_voices(voices, old)
        if new > old + 0.05:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE shots SET duration_sec=? WHERE episode_id=? AND shot_num=?",
                    (new, episode_id, sn),
                )
            changed.append({"shot_num": sn, "from": old, "to": new})
            print(
                f"[duration] shot {sn}: {old:.1f}s -> {new:.1f}s (cover dialogue)",
                flush=True,
            )
    return changed


def _ensure_shot_audio_usable(
    project_id: str,
    episode_id: str,
    shot_num: int,
    mp4: Path,
    *,
    shot_label: str = "",
) -> None:
    """只接受 MiniMax/H3 原生音轨；禁止 Web TTS 混音盖轨。

    无声或坏轨时直接报错，应检查脚本对白/环境声或重做分镜图后再生成。
    """
    label = shot_label or f"镜{shot_num}"
    # 显式禁止任何 Web 侧补音（忽略旧环境变量）
    _ = project_id, episode_id  # 保留签名兼容调用方
    hum = _mp4_audio_is_hum_only(mp4)
    silent = not _mp4_has_audio_stream(mp4)
    if not silent and not hum:
        return
    kind = "电流声/平坦底噪" if hum else "无音轨"
    raise RuntimeError(
        f"{label} MiniMax 原生音轨异常（{kind}）。"
        "已禁止 Web/TTS 混音。请检查该段脚本对白与分镜图是否合理，"
        "必要时重做分镜图后再重跑该段视频。"
    )


def _push_muxed_shots_to_latest_comfy_ckpt(
    project_id: str, ep_num: int, shot_nums: List[int]
) -> None:
    """（已停用）旧版把 Web 补音片写回 Comfy ckpt。"""
    return
    ckpt_root = comfy_root() / "output" / "drama_ui" / "ckpt"
    if not ckpt_root.is_dir():
        return
    best: Optional[Path] = None
    best_mtime = 0.0
    for d in ckpt_root.iterdir():
        if not d.is_dir():
            continue
        mp4_dir = d / "mp4"
        if not mp4_dir.is_dir():
            continue
        newest = 0.0
        for p in mp4_dir.glob("shot_*.mp4"):
            if p.is_file():
                newest = max(newest, p.stat().st_mtime)
        if newest > best_mtime:
            best_mtime = newest
            best = d
    if not best:
        return
    src_dir = (
        db.project_dir(project_id) / "episodes" / f"ep{int(ep_num):02d}" / "shots"
    )
    mp4_dir = best / "mp4"
    mp4_dir.mkdir(parents=True, exist_ok=True)
    for sn in shot_nums:
        src = src_dir / f"shot_{int(sn):02d}.mp4"
        if src.is_file():
            copy_into_project(src, mp4_dir / f"shot_{int(sn):02d}.mp4")


def _best_comfy_shot_mp4_src(
    shot_num: int,
    *,
    prefer_audio: bool = False,
) -> Optional[Path]:
    """从所有 ckpt 中为某一镜挑选 mp4；单镜成片优先带音轨版本，避免被整集无声 i2v 覆盖。"""
    ckpt_root = comfy_root() / "output" / "drama_ui" / "ckpt"
    if not ckpt_root.is_dir():
        return None
    sn = int(shot_num)
    candidates: List[Path] = []
    for d in ckpt_root.iterdir():
        if not d.is_dir():
            continue
        p = d / "mp4" / f"shot_{sn:02d}.mp4"
        if p.is_file() and p.stat().st_size > 512:
            candidates.append(p)
    if not candidates:
        return None

    def rank(p: Path) -> tuple:
        has_a = _mp4_has_audio_stream(p)
        if prefer_audio:
            return (1 if has_a else 0, p.stat().st_mtime)
        return (p.stat().st_mtime, 1 if has_a else 0)

    return max(candidates, key=rank)


def _best_comfy_shot_mp4_dir(n_shots: int) -> Optional[Path]:
    """Pick newest Comfy ckpt mp4/ dir that has at least one shot segment."""
    ckpt_root = comfy_root() / "output" / "drama_ui" / "ckpt"
    if not ckpt_root.is_dir() or n_shots <= 0:
        return None
    best: Optional[Path] = None
    best_mtime = 0.0
    for d in ckpt_root.iterdir():
        if not d.is_dir():
            continue
        mp4_dir = d / "mp4"
        if not mp4_dir.is_dir():
            continue
        newest = 0.0
        found = 0
        for i in range(1, n_shots + 1):
            p = mp4_dir / f"shot_{i:02d}.mp4"
            if p.is_file() and p.stat().st_size > 512:
                found += 1
                newest = max(newest, p.stat().st_mtime)
        if found and newest > best_mtime:
            best_mtime = newest
            best = mp4_dir
    return best


def _import_shot_video_from_history(
    history: Optional[Dict[str, Any]],
    project_id: str,
    ep_num: int,
    shot_num: int,
    *,
    vid_prefix: str = "",
) -> Optional[Path]:
    """把单镜 Comfy SaveVideo 输出经 /view 拉到项目 episodes/…/shots/shot_XX.mp4。"""
    dest = (
        db.project_dir(project_id)
        / "episodes"
        / f"ep{int(ep_num):02d}"
        / "shots"
        / f"shot_{int(shot_num):02d}.mp4"
    )
    src: Optional[Path] = None
    if history:
        try:
            _imgs, vids = resolve_output_files(history, client=ComfyClient())
            if vids:
                src = vids[0]
        except Exception:
            src = None
    if (src is None or not src.is_file()) and vid_prefix:
        src = find_newest_under(vid_prefix, ".mp4")
    if src is None or not src.is_file():
        # 云 GPU 镜像目录按 SaveVideo 编号落盘时的兜底
        mirror = (
            comfy_root()
            / "output"
            / "bingjing"
            / project_id[:8]
            / f"ep{int(ep_num):02d}"
        )
        if mirror.is_dir():
            cands = sorted(
                mirror.glob("final_*.mp4"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            # 优先按序号对齐镜号
            numbered = mirror / f"final_{int(shot_num):05d}_.mp4"
            if numbered.is_file():
                src = numbered
            elif cands:
                src = cands[0]
    if src is None or not src.is_file():
        return None
    copy_into_project(src, dest)
    return dest


def _import_shot_videos_from_comfy_ckpt(
    project_id: str,
    ep_num: int,
    n_shots: int,
    *,
    shot_nums: Optional[List[int]] = None,
    prefer_audio: bool = False,
) -> None:
    base = db.project_dir(project_id)
    dest_dir = base / "episodes" / f"ep{ep_num:02d}" / "shots"
    dest_dir.mkdir(parents=True, exist_ok=True)
    nums = (
        [int(x) for x in shot_nums]
        if shot_nums
        else list(range(1, int(n_shots) + 1))
    )
    for sn in nums:
        src = _best_comfy_shot_mp4_src(sn, prefer_audio=prefer_audio)
        if not src:
            continue
        dest = dest_dir / f"shot_{sn:02d}.mp4"
        src_has_a = _mp4_has_audio_stream(src)
        dest_has_a = dest.is_file() and _mp4_has_audio_stream(dest)
        if dest.is_file() and not src_has_a and dest_has_a:
            continue
        if dest.is_file() and dest_has_a:
            from .shot_audio_mux import _needs_audio_remux

            if not _needs_audio_remux(dest):
                if src.stat().st_mtime <= dest.stat().st_mtime + 2:
                    continue
                if src.stat().st_size <= dest.stat().st_size:
                    continue
        if (
            not dest.is_file()
            or src.stat().st_mtime > dest.stat().st_mtime + 1
            or (prefer_audio and src_has_a and not dest_has_a)
        ):
            copy_into_project(src, dest)


def _reconcile_shot_videos_from_disk(
    project_id: str, episode_id: str, ep_num: int, shots: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    base = db.project_dir(project_id)
    shots_dir = base / "episodes" / f"ep{ep_num:02d}" / "shots"
    out: List[Dict[str, Any]] = []
    for s in shots:
        row = dict(s)
        rel = (row.get("video_path") or "").strip()
        cand = shots_dir / f"shot_{int(row['shot_num']):02d}.mp4"
        if cand.is_file():
            rel = str(cand.relative_to(base)).replace("\\", "/")
            if (row.get("video_path") or "") != rel:
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE shots SET video_path=? WHERE episode_id=? AND shot_num=?",
                        (rel, episode_id, int(row["shot_num"])),
                    )
            row["video_path"] = rel
        out.append(row)
    return out


def _reconcile_shot_images_from_disk(
    project_id: str, episode_id: str, ep_num: int, shots: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """磁盘上已有 shot_XX.png 时补写 image_path（避免保存镜头表后路径丢失）。"""
    base = db.project_dir(project_id)
    ep_tag = f"ep{ep_num:02d}"
    shots_dir = base / "episodes" / ep_tag / "shots"
    out: List[Dict[str, Any]] = []
    for s in shots:
        row = dict(s)
        rel = (row.get("image_path") or "").strip()
        if not rel or not _project_file_exists(project_id, rel):
            cand = shots_dir / f"shot_{int(row['shot_num']):02d}.png"
            if cand.is_file():
                rel = str(cand.relative_to(base)).replace("\\", "/")
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE shots SET image_path=? WHERE episode_id=? AND shot_num=?",
                        (rel, episode_id, int(row["shot_num"])),
                    )
                row["image_path"] = rel
        out.append(row)
    return out


def _project_file_exists(project_id: str, rel_path: str) -> bool:
    if not rel_path:
        return False
    base = db.project_dir(project_id).resolve()
    target = (base / rel_path).resolve()
    return str(target).startswith(str(base)) and target.is_file()


def _normalize_shot_api(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    try:
        out["voices"] = json.loads(out.get("voices_json") or "[]")
    except json.JSONDecodeError:
        out["voices"] = []
    if not isinstance(out["voices"], list):
        out["voices"] = []
    return out


def get_episode(episode_id: str) -> Dict[str, Any]:
    with db.connect() as conn:
        ep = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not ep:
            raise KeyError("episode not found")
        shots = conn.execute(
            "SELECT * FROM shots WHERE episode_id=? ORDER BY shot_num",
            (episode_id,),
        ).fetchall()
    out = dict(ep)
    pid = out["project_id"]
    ep_num = int(out["ep_num"])
    shot_list = [_normalize_shot_api(dict(s)) for s in shots]
    # 仅协调本项目磁盘文件；禁止在「读取分集」时从全局 Comfy ckpt / 其它项目拷贝媒体
    from .asset_recovery import is_bingjing_project_id

    if is_bingjing_project_id(pid):
        ensure_episode_assets(pid, episode_id, ep_num, len(shot_list))
    out["shots"] = _reconcile_shot_images_from_disk(
        pid, episode_id, ep_num, shot_list
    )
    out["shots"] = _reconcile_shot_videos_from_disk(
        pid, episode_id, ep_num, out["shots"]
    )
    prompt = (out.get("drama10_prompt") or "").strip()
    if not prompt:
        prompt = read_drama10_export_file(pid, ep_num)
    if prompt:
        out["shots"] = merge_shots_from_drama10_prompt(
            out["shots"], prompt, only_fill_empty=True
        )
    # 整段 Drama10 仅用于 Comfy 导出文件，不在集编辑窗底部重复展示
    out["drama10_prompt"] = ""
    from .voice_cast import normalize_cast

    prefs = get_generation_prefs(pid)
    vc = normalize_cast(prefs.get("voice_cast"))
    out["voice_cast"] = vc
    # 人物小传仅用本项目文档，绝不读共享 web/data/epXX.json（那是冰镜专用）
    docs = get_project(pid).get("docs") or {}
    out["character_bible"] = (
        (docs.get("series_scripts_note") or "").strip()
        or (prefs.get("global_memory") or "").strip()
    )
    return out


def update_episode(episode_id: str, **fields: Any) -> Dict[str, Any]:
    allowed = (
        "title",
        "outline",
        "script_md",
        "storyboard_md",
        "drama10_prompt",
        "video_path",
        "pipeline_json",
    )
    parts = []
    vals: List[Any] = []
    for k, v in fields.items():
        if k in allowed and v is not None:
            parts.append(f"{k}=?")
            if k == "pipeline_json":
                vals.append(json.dumps(v, ensure_ascii=False))
            else:
                vals.append(v)
    if parts:
        vals.append(episode_id)
        with db.connect() as conn:
            conn.execute(f"UPDATE episodes SET {', '.join(parts)} WHERE id=?", vals)
    return get_episode(episode_id)


def replace_shots(episode_id: str, shots: List[Dict[str, Any]]) -> Dict[str, Any]:
    from .voice_cast import enrich_voice_line, merge_voice_cast

    with db.connect() as conn:
        old_rows = conn.execute(
            "SELECT * FROM shots WHERE episode_id=? ORDER BY shot_num",
            (episode_id,),
        ).fetchall()
        ep_row = conn.execute(
            "SELECT project_id FROM episodes WHERE id=?",
            (episode_id,),
        ).fetchone()
    project_id = ep_row["project_id"] if ep_row else ""
    voice_cast = (
        merge_voice_cast(get_generation_prefs(project_id).get("voice_cast"), None)
        if project_id
        else []
    )
    old_map = {int(r["shot_num"]): dict(r) for r in old_rows}
    merged: List[Dict[str, Any]] = []
    for s in shots:
        sn = int(s["shot_num"])
        prev = old_map.get(sn) or {}
        # 新脚本若显式带 voices（含空列表）必须以新为准，勿回填旧对白——
        # 否则重写脚本后仍会把旧的长台词塞进新段，语速被撑爆。
        voices_in = s.get("voices")
        if voices_in is None:
            try:
                voices_in = json.loads(prev.get("voices_json") or "[]")
            except json.JSONDecodeError:
                voices_in = []
        if isinstance(voices_in, list) and voice_cast:
            voices_in = [
                enrich_voice_line(v, voice_cast)
                for v in voices_in
                if isinstance(v, dict)
            ]
        new_visual = (s.get("visual") or "").strip()
        old_visual = (prev.get("visual") or "").strip()
        try:
            old_voices = json.loads(prev.get("voices_json") or "[]")
        except json.JSONDecodeError:
            old_voices = []
        new_voices = voices_in if isinstance(voices_in, list) else []
        script_changed = (new_visual and old_visual and new_visual != old_visual) or (
            json.dumps(new_voices, ensure_ascii=False)
            != json.dumps(old_voices, ensure_ascii=False)
        )
        keep_video = s.get("video_path") or ""
        if not keep_video:
            # 脚本/台词变了则丢掉旧成片，避免画面与对白对不上
            keep_video = "" if script_changed else (prev.get("video_path") or "")
        merged.append(
            compact_shot_record(
                {
                    **s,
                    "image_path": s.get("image_path") or prev.get("image_path") or "",
                    "video_path": keep_video,
                    "title": s.get("title") if s.get("title") is not None else prev.get("title") or "",
                    "scene": s.get("scene") if s.get("scene") is not None else prev.get("scene") or "",
                    "motion": s.get("motion") if s.get("motion") is not None else prev.get("motion") or "",
                    "performance": s.get("performance")
                    if s.get("performance") is not None
                    else prev.get("performance") or "",
                    "continuity": s.get("continuity")
                    if s.get("continuity") is not None
                    else prev.get("continuity") or "",
                    "ambience": s.get("ambience")
                    if s.get("ambience") is not None
                    else prev.get("ambience") or "",
                    "voices": voices_in if isinstance(voices_in, list) else [],
                }
            )
        )
    with db.connect() as conn:
        conn.execute("DELETE FROM shots WHERE episode_id=?", (episode_id,))
        for s in merged:
            prev_row = old_map.get(int(s["shot_num"]), {})
            conn.execute(
                """
                INSERT INTO shots (
                    id, episode_id, shot_num, duration_sec, title, visual, scene, motion,
                    performance, continuity, ambience, voices_json, image_path, video_path
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    s.get("id") or prev_row.get("id") or str(uuid.uuid4()),
                    episode_id,
                    int(s["shot_num"]),
                    float(s.get("duration_sec", 5)),
                    s.get("title", ""),
                    s.get("visual", ""),
                    s.get("scene", ""),
                    s.get("motion", ""),
                    s.get("performance", ""),
                    s.get("continuity", ""),
                    s.get("ambience", ""),
                    json.dumps(s.get("voices", []), ensure_ascii=False),
                    s.get("image_path", ""),
                    s.get("video_path", ""),
                ),
            )
    ep = get_episode(episode_id)
    try:
        prompt = _build_drama10_prompt(ep["project_id"], episode_id, for_video=True)
        save_drama10_export_file(ep["project_id"], int(ep["ep_num"]), prompt)
        update_episode(episode_id, drama10_prompt=prompt)
    except Exception:
        pass
    return get_episode(episode_id)


def generate_series_scripts_via_ohmygpt(
    project_id: str,
    *,
    from_ep: int = 1,
    to_ep: int = 20,
    model: Optional[str] = None,
    regenerate_outline: bool = True,
    sync_to_db: bool = True,
) -> Dict[str, Any]:
    """OhMyGPT 聊天模型生成 web/data/epXX.json；可选同步镜头表（不触发分镜/视频）。"""
    import re

    from . import drama10_script_llm as script_llm

    proj = get_project(project_id)
    ec = int(proj.get("episode_count") or 20)
    from_ep = max(1, int(from_ep))
    to_ep = min(int(to_ep), ec)
    if to_ep < from_ep:
        raise ValueError("集数范围无效")

    outline_path = db.WEB_ROOT / "data" / "series_outline.json"
    outline: Optional[Dict[str, Any]] = None
    if regenerate_outline:
        outline = script_llm.generate_series_outline(episode_count=ec, model=model)
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        outline_path.write_text(
            json.dumps(outline, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif outline_path.is_file():
        outline = json.loads(outline_path.read_text(encoding="utf-8"))

    paths = script_llm.generate_and_save_episodes(
        from_ep,
        to_ep,
        outline=outline,
        model=model,
    )
    synced: List[int] = []
    if sync_to_db:
        for p in paths:
            m = re.match(r"ep(\d+)\.json$", p.name)
            if not m:
                continue
            n = int(m.group(1))
            sync_episode_from_json_file(project_id, n)
            synced.append(n)

    return {
        "model": script_llm.script_model(model),
        "from_ep": from_ep,
        "to_ep": to_ep,
        "files": [str(p.relative_to(db.WEB_ROOT)).replace("\\", "/") for p in paths],
        "outline_file": (
            str(outline_path.relative_to(db.WEB_ROOT)).replace("\\", "/")
            if outline_path.is_file()
            else ""
        ),
        "synced_episodes": synced,
    }


def sync_episode_from_json_file(project_id: str, ep_num: int) -> Dict[str, Any]:
    """用 web/data/epXX.json 覆盖镜头表（保留已有 image_path）。"""
    from .drama10_export import load_episode as load_ep_json

    legacy = load_ep_json(int(ep_num))
    from .voice_cast import enrich_episode_shots, merge_voice_cast, sync_voice_cast_to_characters

    merged_cast = merge_voice_cast(
        get_generation_prefs(project_id).get("voice_cast"),
        legacy.get("voice_cast"),
    )
    if merged_cast:
        legacy = enrich_episode_shots(legacy, merged_cast)
    eps = list_episodes(project_id)
    ep_row = next((e for e in eps if int(e["ep_num"]) == int(ep_num)), None)
    if not ep_row:
        raise KeyError(f"EP{ep_num:02d} 不存在")
    eid = ep_row["id"]
    shots = []
    for s in legacy.get("shots") or []:
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
                "voices": s.get("voices") or [],
            }
        )
    replace_shots(eid, shots)
    prompt = _build_drama10_prompt(project_id, eid, for_video=True)
    save_drama10_export_file(project_id, int(ep_num), prompt)
    patch: Dict[str, Any] = {
        "title": legacy.get("title") or ep_row.get("title") or "",
        "drama10_prompt": prompt,
    }
    if legacy.get("script_md"):
        patch["script_md"] = legacy.get("script_md")
    if legacy.get("storyboard_md"):
        patch["storyboard_md"] = legacy.get("storyboard_md")
    update_episode(eid, **patch)
    if merged_cast:
        sync_voice_cast_to_characters(project_id, merged_cast, episode_id="")
    sync_episode_production_assets_from_json(project_id, legacy)
    return get_episode(eid)


def sync_episode_production_assets_from_json(
    project_id: str, legacy: Dict[str, Any]
) -> None:
    """将 epXX.json 中的 characters / scenes 同步到人物库与场景库。"""
    for i, c in enumerate(legacy.get("characters") or []):
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if not name or name == "旁白":
            continue
        upsert_character(
            project_id,
            {
                "name": name,
                "role_note": (c.get("role_note") or "").strip(),
                "visual_prompt": (c.get("visual_prompt") or "").strip(),
                "voice_desc": (c.get("voice_desc") or "").strip(),
                "tts_voice": (c.get("tts_voice") or "").strip(),
                "sort_order": i,
            },
        )
    for j, sc in enumerate(legacy.get("scenes") or []):
        if not isinstance(sc, dict):
            continue
        name = (sc.get("name") or "").strip()
        if not name:
            continue
        upsert_scene(
            project_id,
            {
                "code": (sc.get("code") or "").strip(),
                "name": name,
                "visual_prompt": (sc.get("visual_prompt") or "").strip(),
                "sort_order": j,
            },
        )


def _still_prompts_from_legacy_dict(legacy: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for s in legacy.get("shots") or []:
        if not isinstance(s, dict):
            continue
        sp = (s.get("still_prompt") or "").strip()
        if not sp:
            continue
        sn = int(s.get("id") or s.get("shot_num") or 0)
        if sn:
            out[str(sn)] = sp
    return out


def still_prompt_overrides_from_episode_json(ep_num: int) -> Dict[str, str]:
    """兼容旧调用：仅读共享 web/data/epXX.json（冰镜专用）。"""
    from .drama10_export import load_episode

    try:
        return _still_prompts_from_legacy_dict(load_episode(int(ep_num)))
    except (OSError, ValueError, TypeError, FileNotFoundError):
        return {}


def still_prompt_overrides_for_project(project_id: str, ep_num: int) -> Dict[str, str]:
    """按项目隔离读取 still_prompt，禁止非冰镜项目误用共享 epXX.json。"""
    from .asset_recovery import is_bingjing_project_id

    ep_num = int(ep_num)
    candidates: List[Path] = [
        db.project_dir(project_id) / "scripts" / f"ep{ep_num:02d}.json",
    ]
    # 压测短剧本地数据文件（未拷进 projects/scripts 时的回退）
    if project_id == "gpu-vram-test-manhua":
        candidates.append(db.WEB_ROOT / "data" / f"gpu_vram_test_ep{ep_num:02d}.json")
    if is_bingjing_project_id(project_id):
        candidates.append(db.WEB_ROOT / "data" / f"ep{ep_num:02d}.json")

    for path in candidates:
        if not path.is_file():
            continue
        try:
            legacy = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if isinstance(legacy, dict):
            out = _still_prompts_from_legacy_dict(legacy)
            if out:
                return out
    return {}


def _default_project_pipeline() -> Dict[str, str]:
    return {s["id"]: "pending" for s in PROJECT_STAGES}


def _default_episode_pipeline() -> Dict[str, str]:
    return {s["id"]: "pending" for s in EPISODE_STAGES}


def _consolidate_duplicate_running_jobs(project_id: str) -> None:
    """同集同阶段只允许一条 running（保留最新一条，其余标失败）。"""
    now = db._utcnow()
    with db.connect() as conn:
        groups = conn.execute(
            """
            SELECT episode_id, stage_id
            FROM jobs
            WHERE project_id=? AND status='running' AND episode_id != ''
              AND stage_id IN ('storyboard_images', 'video')
            GROUP BY episode_id, stage_id
            HAVING COUNT(*) > 1
            """,
            (project_id,),
        ).fetchall()
        for g in groups:
            rows = conn.execute(
                """
                SELECT id FROM jobs
                WHERE project_id=? AND episode_id=? AND stage_id=? AND status='running'
                ORDER BY datetime(created_at) DESC, datetime(updated_at) DESC
                """,
                (project_id, g["episode_id"], g["stage_id"]),
            ).fetchall()
            keep_id = rows[0]["id"]
            for r in rows[1:]:
                conn.execute(
                    """
                    UPDATE jobs SET status='failed', progress=0, updated_at=?,
                      message='重复任务已取消（同集仅保留最新一条）'
                    WHERE id=?
                    """,
                    (now, r["id"]),
                )


def _job_still_running(jid: str) -> bool:
    try:
        return get_job(jid).get("status") == "running"
    except KeyError:
        return False


def _parse_shot_scene_seg(shot: Optional[Dict[str, Any]], shot_num: int) -> tuple:
    """从镜头 title（含「场N段M」）解析场号/段号。"""
    title = ""
    if isinstance(shot, dict):
        title = str(shot.get("title") or "")
    m = re.search(r"场\s*(\d+)\s*段\s*(\d+)", title)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1, int(shot_num)


def _segment_progress_label(
    ep_num: int,
    shot: Optional[Dict[str, Any]],
    shot_num: int,
    *,
    index: int = 0,
    total: int = 0,
    prefix: str = "整集",
) -> str:
    scene_i, seg_i = _parse_shot_scene_seg(shot, shot_num)
    base = f"{prefix}-第{int(ep_num)}集第{scene_i}场第{seg_i}段"
    if total and total > 1:
        return f"{base}（{int(index)}/{int(total)}）"
    return base


def cancel_job(job_id: str) -> Dict[str, Any]:
    """用户停止：标记 cancelled，后台循环在下一检查点退出；已落盘段落保留。"""
    job = get_job(job_id)
    st = (job.get("status") or "").lower()
    if st != "running":
        raise ValueError(f"任务未在运行（当前：{st or '未知'}）")
    prog = float(job.get("progress") or 0)
    _job_update(
        job_id,
        "cancelled",
        prog,
        "已停止；已生成段落保留在工作台，可点「继续补全视频」接着做",
    )
    return get_job(job_id)


def _job_mark_current_shot(
    jid: str,
    shot_num: int,
    *,
    ep_num: int,
    shot: Optional[Dict[str, Any]] = None,
    index: int = 0,
    total: int = 0,
    progress: float = 0.0,
    phase: str = "视频生成中",
) -> str:
    """更新整集任务的当前段，供 UI 高亮与文案展示。"""
    label = _segment_progress_label(
        ep_num, shot, shot_num, index=index, total=total, prefix="整集"
    )
    msg = f"{label}{phase}"
    _job_update(
        jid,
        "running",
        progress,
        msg,
        patch_params={
            "current_shot_num": int(shot_num),
            "segment_serial": True,
            "current_label": label,
        },
    )
    return label


def recover_jobs_on_startup() -> None:
    """清理占位假任务、以及服务重启后仍标记 running 的任务（仅改 jobs 表，不篡改集状态）。"""
    interrupted_video: List[tuple] = []
    with db.connect() as conn:
        interrupted_video = [
            (r["project_id"], r["episode_id"], r["params_json"] or "{}")
            for r in conn.execute(
                """
                SELECT project_id, episode_id, params_json FROM jobs
                WHERE status='running' AND stage_id='video' AND episode_id != ''
                """
            ).fetchall()
        ]
        conn.execute(
            """
            UPDATE jobs SET status='failed', progress=0,
              message='历史占位任务（未真正执行 ComfyUI），请重新点击生成'
            WHERE status='done' AND (message LIKE '%占位%' OR message LIKE '%placeholder%')
            """
        )
        conn.execute(
            """
            UPDATE jobs SET status='failed', progress=0,
              message='任务中断（Comfy/制作台重启），将自动 checkpoint 续跑'
            WHERE status='running'
            """
        )

    for project_id, episode_id, params_json in interrupted_video:
        resume_params = _video_job_params_from_row({"params_json": params_json})
        if not (
            resume_params.get("shot_nums")
            or resume_params.get("mux_only")
            or resume_params.get("confirm_full_episode")
        ):
            continue

        def _resume(
            pid: str = project_id,
            eid: str = episode_id,
            params: Dict[str, Any] = resume_params,
        ) -> None:
            time.sleep(10)
            try:
                start_job(pid, "video", episode_id=eid, params=params)
            except Exception:
                pass

        threading.Thread(target=_resume, daemon=True).start()

    if _episode_video_auto_resume_enabled():
        _auto_resume_failed_video_jobs()


def _video_fail_message_retriable(message: str) -> bool:
    msg_raw = message or ""
    if any(
        x in msg_raw
        for x in ("已取消", "手动封装", "改 checkpoint", "改由 API", "仅预览")
    ):
        return False
    msg = msg_raw.lower()
    needles = (
        "队列消失",
        "checkpoint",
        "续跑",
        "oom",
        "killed",
        "中断",
        "connection",
        "不可达",
        "errno 111",
        "errno 104",
        "ffmpeg",
        "xfade",
        "concat",
        "拼接",
        "calledprocesserror",
    )
    return any(n in msg for n in needles)


def _video_job_params_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        p = json.loads(row.get("params_json") or "{}")
        return p if isinstance(p, dict) else {}
    except json.JSONDecodeError:
        return {}


def _episode_video_auto_resume_enabled() -> bool:
    v = os.environ.get("EPISODE_VIDEO_AUTO_RESUME", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _auto_resume_failed_video_jobs() -> None:
    """最近一次成片失败且可续跑时，自动重新排队（Comfy checkpoint 复用）。"""
    if not _episode_video_auto_resume_enabled():
        return
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT j.project_id, j.episode_id, j.message, j.params_json
            FROM jobs j
            INNER JOIN (
                SELECT episode_id, MAX(created_at) AS mx
                FROM jobs WHERE stage_id='video' AND episode_id != ''
                GROUP BY episode_id
            ) t ON j.episode_id=t.episode_id AND j.created_at=t.mx
            WHERE j.status='failed' AND j.stage_id='video'
              AND j.updated_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '+8 hours', '-8 hours')
            """
        ).fetchall()
    for row in rows:
        if not _video_fail_message_retriable(row["message"] or ""):
            continue
        pid, eid = row["project_id"], row["episode_id"]
        with db.connect() as conn:
            busy = conn.execute(
                """
                SELECT 1 FROM jobs
                WHERE episode_id=? AND stage_id='video' AND status='running'
                LIMIT 1
                """,
                (eid,),
            ).fetchone()
        if busy:
            continue

        def _go(
            project_id: str = pid,
            episode_id: str = eid,
            msg: str = row["message"] or "",
            params_json: str = row["params_json"] or "{}",
        ) -> None:
            time.sleep(12)
            mux = _video_fail_message_retriable(msg) and any(
                x in (msg or "").lower() for x in ("ffmpeg", "xfade", "concat")
            )
            base_params = _video_job_params_from_row({"params_json": params_json})
            if base_params.get("shot_nums"):
                return
            if mux:
                resume_params: Dict[str, Any] = {**base_params, "mux_only": True}
            else:
                resume_params = {k: v for k, v in base_params.items() if k != "fresh_video"}
            try:
                start_job(
                    project_id,
                    "video",
                    episode_id=episode_id,
                    params=resume_params,
                )
            except Exception:
                pass

        threading.Thread(target=_go, daemon=True).start()


def _maybe_auto_resume_video_after_fail(
    project_id: str, episode_id: str, exc: BaseException
) -> None:
    if not _episode_video_auto_resume_enabled():
        return
    if not (_comfy_video_retriable(exc) or _video_fail_message_retriable(str(exc))):
        return

    def _go() -> None:
        time.sleep(25)
        with db.connect() as conn:
            busy = conn.execute(
                """
                SELECT 1 FROM jobs
                WHERE episode_id=? AND stage_id='video' AND status='running'
                LIMIT 1
                """,
                (episode_id,),
            ).fetchone()
        if busy:
            return
        msg = str(exc)
        mux = any(x in msg.lower() for x in ("ffmpeg", "xfade", "concat", "拼接"))
        last = _last_job_for_episode(episode_id, "video") or {}
        base_params = _video_job_params_from_row(last)
        if base_params.get("shot_nums"):
            return
        if not base_params.get("mux_only") and not base_params.get("confirm_full_episode"):
            return
        if mux:
            resume_params = {**base_params, "mux_only": True}
        else:
            resume_params = {k: v for k, v in base_params.items() if k != "fresh_video"}
        try:
            start_job(
                project_id,
                "video",
                episode_id=episode_id,
                params=resume_params,
            )
        except Exception:
            pass

    threading.Thread(target=_go, daemon=True).start()


def _last_job_for_episode(episode_id: str, stage_id: str) -> Optional[Dict[str, Any]]:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE episode_id=? AND stage_id=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (episode_id, stage_id),
        ).fetchone()
    return dict(row) if row else None


def _active_job_for_episode(episode_id: str) -> Optional[Dict[str, Any]]:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE episode_id=? AND status='running'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (episode_id,),
        ).fetchone()
    return dict(row) if row else None


def _count_shot_pngs_on_disk(project_id: str, ep_num: int, n_shots: int) -> int:
    if n_shots <= 0:
        return 0
    shots_dir = db.project_dir(project_id) / "episodes" / f"ep{ep_num:02d}" / "shots"
    if not shots_dir.is_dir():
        return 0
    return sum(
        1 for i in range(1, n_shots + 1) if (shots_dir / f"shot_{i:02d}.png").is_file()
    )


def compute_episode_production(
    project_id: str, ep: Dict[str, Any], shots: Optional[List[Dict]] = None
) -> Dict[str, Any]:
    """仅根据磁盘文件、镜头表、任务记录计算真实状态（不读 pipeline_json 缓存）。"""
    episode_id = ep.get("id") or ""
    ep_num = int(ep.get("ep_num") or 0)
    shots = shots if shots is not None else (ep.get("shots") or [])
    if episode_id and shots:
        shots = _reconcile_shot_images_from_disk(project_id, episode_id, ep_num, shots)
        shots = _reconcile_shot_videos_from_disk(project_id, episode_id, ep_num, shots)
    n = len(shots)
    imgs_db = sum(
        1
        for s in shots
        if s.get("image_path") and _project_file_exists(project_id, s["image_path"])
    )
    imgs_disk = _count_shot_pngs_on_disk(project_id, ep_num, n)
    imgs = max(imgs_db, imgs_disk)
    ep_tag = f"ep{ep_num:02d}"
    ep_dir = db.project_dir(project_id) / "episodes" / ep_tag
    video_rel = (ep.get("video_path") or "").strip()
    video_ok = _project_file_exists(project_id, video_rel)
    default_final = ep_dir / f"EP{ep_num:02d}_final.mp4"
    if not video_ok and default_final.is_file():
        video_rel = str(default_final.relative_to(db.project_dir(project_id))).replace(
            "\\", "/"
        )
        video_ok = True

    pl = _default_episode_pipeline()

    script = (ep.get("script_md") or "").strip()
    if len(script) > 40 and "见服务器" not in script[:80]:
        pl["episode_script"] = "done"
    elif script:
        pl["episode_script"] = "running"

    sb_md = (ep.get("storyboard_md") or "").strip()
    drama10 = (ep.get("drama10_prompt") or "").strip()
    if sb_md:
        pl["storyboard_script"] = "done"
    elif drama10 and n >= 1:
        pl["storyboard_script"] = "done"
    elif drama10 or n >= 1:
        pl["storyboard_script"] = "running"

    if n == 0:
        pl["storyboard_images"] = "pending"
    elif imgs >= n and n > 0:
        pl["storyboard_images"] = "done"
    elif imgs > 0:
        pl["storyboard_images"] = "running"

    if video_ok:
        pl["video"] = "done"

    def _apply_job(stage_key: str, pl_key: Optional[str] = None) -> None:
        pk = pl_key or stage_key
        if pl.get(pk) == "done":
            return
        last = _last_job_for_episode(episode_id, stage_key)
        if not last:
            return
        if last["status"] == "running":
            pl[pk] = "running"
        elif last["status"] == "failed" and pl.get(pk) == "pending":
            pl[pk] = "failed"

    _apply_job("storyboard_images")
    _apply_job("video")
    _apply_job("drama10_export", "storyboard_script")

    active = _active_job_for_episode(episode_id)
    if active:
        sid = active["stage_id"]
        if sid == "drama10_export" and pl.get("storyboard_script") != "done":
            pl["storyboard_script"] = "running"
        elif sid in pl and pl.get(sid) != "done":
            pl[sid] = "running"

    collage_rel = ""
    collage_mtime = 0
    shot_image_mtimes: Dict[int, int] = {}
    shot_video_mtimes: Dict[int, int] = {}
    shots_dir = ep_dir / "shots"
    for i in range(1, max(n, 1) + 1):
        p = shots_dir / f"shot_{i:02d}.png"
        if p.is_file():
            shot_image_mtimes[i] = int(p.stat().st_mtime)
        vp = shots_dir / f"shot_{i:02d}.mp4"
        if vp.is_file():
            shot_video_mtimes[i] = int(vp.stat().st_mtime)

    return {
        "pipeline": pl,
        "detail": {
            "shots_total": n,
            "shots_with_image": imgs,
            "collage_path": collage_rel,
            "collage_mtime": collage_mtime,
            "shot_image_mtimes": shot_image_mtimes,
            "shot_video_mtimes": shot_video_mtimes,
            "video_path": video_rel if video_ok else (ep.get("video_path") or ""),
            "video_ready": video_ok,
            "video_mtime": (
                int((db.project_dir(project_id) / video_rel).stat().st_mtime)
                if video_ok and video_rel
                else 0
            ),
            "active_job": active,
            "last_jobs": {
                "storyboard_images": _last_job_for_episode(ep["id"], "storyboard_images"),
                "video": _last_job_for_episode(ep["id"], "video"),
                "drama10_export": _last_job_for_episode(ep["id"], "drama10_export"),
            },
        },
    }


def compute_project_status(project_id: str) -> Dict[str, Any]:
    _consolidate_duplicate_running_jobs(project_id)
    proj = get_project(project_id)
    docs = proj.get("docs") or {}
    try:
        pp = json.loads(docs.get("pipeline_json") or "{}")
    except json.JSONDecodeError:
        pp = _default_project_pipeline()

    chars = list_characters(project_id)
    scenes = list_scenes(project_id)
    if docs.get("outline_md"):
        pp["outline"] = "done"
    if chars:
        pp["characters"] = "done" if len(chars) >= 1 else "pending"
    if scenes:
        pp["scenes"] = "done" if len(scenes) >= 1 else "pending"
    eps = list_episodes(project_id)
    if any(e.get("script_md") for e in eps):
        pp["series_scripts"] = "running" if len([e for e in eps if e.get("script_md")]) < len(eps) else "done"

    running_job = _active_job(project_id)

    ep_statuses = []
    for e in eps:
        ep_statuses.append(compute_episode_status(e["id"], e, project_id))

    return {
        "project_id": project_id,
        "project_pipeline": pp,
        "project_stages": PROJECT_STAGES,
        "episode_stages": EPISODE_STAGES,
        "episodes": ep_statuses,
        "active_job": running_job,
        "active_jobs": _active_jobs(project_id),
    }


def compute_episode_status(
    episode_id: str, ep_row: Optional[Dict] = None, project_id: Optional[str] = None
) -> Dict[str, Any]:
    ep = ep_row or get_episode(episode_id)
    pid = project_id or ep.get("project_id")
    if not pid:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM episodes WHERE id=?", (episode_id,)
            ).fetchone()
        pid = row["project_id"] if row else ""
    if ep_row and "shots" not in ep:
        with db.connect() as conn:
            shots = conn.execute(
                "SELECT * FROM shots WHERE episode_id=? ORDER BY shot_num",
                (episode_id,),
            ).fetchall()
        ep = {**ep, "shots": [dict(s) for s in shots]}
    prod = compute_episode_production(pid, ep)

    return {
        "episode_id": episode_id,
        "ep_num": ep.get("ep_num"),
        "title": ep.get("title"),
        "pipeline": prod["pipeline"],
        "detail": prod["detail"],
    }


def _active_jobs(project_id: str) -> List[Dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT j.*, e.ep_num, e.title AS ep_title
            FROM jobs j
            LEFT JOIN episodes e ON e.id = j.episode_id
            WHERE j.project_id=? AND j.status='running'
            ORDER BY j.updated_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _active_job(project_id: str) -> Optional[Dict[str, Any]]:
    jobs = _active_jobs(project_id)
    return jobs[0] if jobs else None


def list_jobs(project_id: str, limit: int = 30) -> List[Dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT j.*, e.ep_num, e.title AS ep_title
            FROM jobs j
            LEFT JOIN episodes e ON e.id = j.episode_id
            WHERE j.project_id=?
            ORDER BY j.created_at DESC LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _allow_server_batch_jobs() -> bool:
    v = os.environ.get("ALLOW_SERVER_BATCH_JOBS", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _shot_still_png_path(project_id: str, ep_num: int, shot_num: int) -> Path:
    ep_tag = f"ep{int(ep_num):02d}"
    return (
        db.project_dir(project_id)
        / "episodes"
        / ep_tag
        / "shots"
        / f"shot_{int(shot_num):02d}.png"
    )


def _shot_video_mp4_path(project_id: str, ep_num: int, shot_num: int) -> Path:
    ep_tag = f"ep{int(ep_num):02d}"
    return (
        db.project_dir(project_id)
        / "episodes"
        / ep_tag
        / "shots"
        / f"shot_{int(shot_num):02d}.mp4"
    )


def _shot_asset_ready(path: Path, *, min_bytes: int = 256) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def _assert_shot_stills_for_video(
    project_id: str,
    episode_id: str,
    *,
    shot_nums: Optional[List[int]] = None,
    mux_only: bool = False,
) -> None:
    """图生视频必须已有分镜 PNG；禁止在成片任务里自动出静帧。"""
    if mux_only:
        return
    ep = get_episode(episode_id)
    ep_num = int(ep["ep_num"])
    if shot_nums:
        want = sorted({int(x) for x in shot_nums})
    else:
        want = [
            int(s["shot_num"])
            for s in (ep.get("shots") or [])
            if not _is_black_shot((s.get("visual") or ""))
        ] or [int(s["shot_num"]) for s in (ep.get("shots") or [])]
    missing = [sn for sn in want if not _shot_still_png_path(project_id, ep_num, sn).is_file()]
    if missing:
        raise ValueError(
            f"镜 {missing} 还没有分镜图。"
            "整集重新推理后段数可能变多，请先点「生成分镜图」或「批量补缺分镜图」，"
            "确认图片后再生成视频。"
        )


def start_job(
    project_id: str,
    stage_id: str,
    episode_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    params = params or {}
    shot_nums = params.get("shot_nums")
    if stage_id == "storyboard_images" and not shot_nums:
        if not params.get("confirm_full_episode") and not _allow_server_batch_jobs():
            raise ValueError(
                "整集分镜出图需 confirm_full_episode=true（网页点「整集分镜出图」会带上）；"
                "单镜请传 shot_nums"
            )
    if stage_id == "video" and not shot_nums:
        if (
            not params.get("confirm_full_episode")
            and not params.get("mux_only")
            and not _allow_server_batch_jobs()
        ):
            raise ValueError(
                "整集成片需 confirm_full_episode=true；单镜请传 shot_nums"
            )
    if stage_id == "video" and episode_id and not _allow_server_batch_jobs():
        if params.get("mux_only"):
            pass
        elif shot_nums:
            _assert_shot_stills_for_video(
                project_id,
                episode_id,
                shot_nums=[int(x) for x in shot_nums],
                mux_only=False,
            )
        elif (
            bool(params.get("skip_existing", True))
            and not bool(params.get("fresh_video"))
            and not params.get("rerun_shot_nums")
            and not params.get("rerun_shots")
        ):
            # 批量补缺：只检查「还缺视频」的镜是否已有分镜图
            ep0 = get_episode(episode_id)
            ep_num0 = int(ep0["ep_num"])
            need_stills: List[int] = []
            for s in ep0.get("shots") or []:
                sn = int(s["shot_num"])
                if _is_black_shot((s.get("visual") or "")):
                    continue
                if not _shot_asset_ready(_shot_video_mp4_path(project_id, ep_num0, sn)):
                    need_stills.append(sn)
            if need_stills:
                _assert_shot_stills_for_video(
                    project_id,
                    episode_id,
                    shot_nums=need_stills,
                    mux_only=False,
                )
        else:
            _assert_shot_stills_for_video(
                project_id,
                episode_id,
                shot_nums=None,
                mux_only=False,
            )
    jid = str(uuid.uuid4())
    now = db._utcnow()
    msg = "排队中"
    if params.get("shot_nums"):
        msg = f"排队中 · 单镜 {params['shot_nums']}"
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if stage_id in ("storyboard_images", "video"):
            if not (episode_id or "").strip():
                raise ValueError("分镜出图/成片需指定集数")
            # 同阶段互斥；出图与出视频可并行（分镜图生成后可边看边跑视频）
            busy = conn.execute(
                """
                SELECT id, stage_id FROM jobs
                WHERE status='running'
                  AND stage_id=?
                  AND episode_id=?
                LIMIT 1
                """,
                (stage_id, episode_id),
            ).fetchone()
            if busy:
                label = "分镜出图" if stage_id == "storyboard_images" else "成片/图生视频"
                raise RuntimeError(
                    f"本集已有{label}任务在跑，请等其完成后再提交同类型任务（出图与出视频可同时进行）"
                )
        conn.execute(
            """
            INSERT INTO jobs (id, project_id, episode_id, stage_id, status, progress, message, created_at, updated_at, params_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                jid,
                project_id,
                episode_id or "",
                stage_id,
                "running",
                0,
                msg,
                now,
                now,
                json.dumps(params, ensure_ascii=False),
            ),
        )

    t = threading.Thread(
        target=_run_job,
        args=(jid, project_id, stage_id, episode_id),
        daemon=True,
    )
    t.start()
    return get_job(jid)


def get_job(job_id: str) -> Dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise KeyError("job not found")
    return dict(row)


def _job_update(
    jid: str,
    status: str,
    progress: float,
    message: str,
    *,
    patch_params: Optional[Dict[str, Any]] = None,
) -> None:
    now = db._utcnow()
    with db.connect() as conn:
        if patch_params:
            row = conn.execute(
                "SELECT params_json, status FROM jobs WHERE id=?", (jid,)
            ).fetchone()
            if not row:
                return
            # 用户已停止则不再被后台进度覆盖成 running
            if (row["status"] or "") == "cancelled" and status == "running":
                return
            try:
                params = json.loads(row["params_json"] or "{}")
            except json.JSONDecodeError:
                params = {}
            if not isinstance(params, dict):
                params = {}
            params.update(patch_params)
            conn.execute(
                """
                UPDATE jobs SET status=?, progress=?, message=?, updated_at=?, params_json=?
                WHERE id=?
                """,
                (
                    status,
                    progress,
                    message,
                    now,
                    json.dumps(params, ensure_ascii=False),
                    jid,
                ),
            )
        else:
            row = conn.execute(
                "SELECT status FROM jobs WHERE id=?", (jid,)
            ).fetchone()
            if row and (row["status"] or "") == "cancelled" and status == "running":
                return
            conn.execute(
                "UPDATE jobs SET status=?, progress=?, message=?, updated_at=? WHERE id=?",
                (status, progress, message, now, jid),
            )


def _run_job(
    jid: str, project_id: str, stage_id: str, episode_id: Optional[str]
) -> None:
    try:
        if stage_id != "video":
            _job_update(jid, "running", 0.05, f"开始：{stage_id}")
        if stage_id == "drama10_export" and episode_id:
            _job_drama10_export(jid, project_id, episode_id)
        elif stage_id == "storyboard_images" and episode_id:
            job = get_job(jid)
            try:
                params = json.loads(job.get("params_json") or "{}")
            except json.JSONDecodeError:
                params = {}
            _job_storyboard_images(jid, project_id, episode_id, params)
        elif stage_id == "video" and episode_id:
            job = get_job(jid)
            try:
                vparams = json.loads(job.get("params_json") or "{}")
            except json.JSONDecodeError:
                vparams = {}
            sn = vparams.get("shot_nums")
            if isinstance(sn, list) and sn:
                _job_update(
                    jid,
                    "running",
                    0.05,
                    f"开始 · 单段视频 {sn}",
                )
            else:
                _job_update(
                    jid,
                    "running",
                    0.05,
                    "整集任务已排队 · 将按场/段依次生成",
                    patch_params={"segment_serial": True},
                )
            _job_episode_video(jid, project_id, episode_id, vparams)
        else:
            raise ValueError(f"未知或未支持的阶段：{stage_id}")
    except Exception as e:
        try:
            if get_job(jid).get("status") == "cancelled":
                return
        except KeyError:
            pass
        _job_update(jid, "failed", 0, str(e))
        if stage_id == "video" and episode_id:
            _maybe_auto_resume_video_after_fail(project_id, episode_id, e)


def _enrich_voices_from_assets(
    project_id: str, voices: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """把资产库音色/外形挂到对白行，供 Drama10 / TTS 一致引用。"""
    if not voices:
        return []
    by_name = {
        (c.get("name") or "").strip(): c for c in list_characters(project_id) if c.get("name")
    }
    out: List[Dict[str, Any]] = []
    for v in voices:
        if not isinstance(v, dict):
            continue
        item = dict(v)
        role = (item.get("role") or "").strip()
        ch = by_name.get(role)
        if ch:
            if not (item.get("voice_desc") or "").strip():
                item["voice_desc"] = (ch.get("voice_desc") or "").strip()
            if not (item.get("tts_voice") or "").strip():
                item["tts_voice"] = (ch.get("tts_voice") or "").strip()
            item["asset_visual"] = (ch.get("visual_prompt") or "").strip()[:220]
        out.append(item)
    return out


def _asset_lock_bible(project_id: str, shots: List[Dict[str, Any]]) -> str:
    """从镜头画面/对白中解析出现的人物/场景/道具，拼资产库锁定块。"""
    blob_parts: List[str] = []
    for s in shots:
        blob_parts.append(str(s.get("visual") or ""))
        blob_parts.append(str(s.get("scene") or ""))
        blob_parts.append(str(s.get("title") or ""))
        for v in s.get("voices") or []:
            if isinstance(v, dict):
                blob_parts.append(str(v.get("role") or ""))
                blob_parts.append(str(v.get("text") or ""))
    blob = "\n".join(blob_parts)
    lines: List[str] = []
    char_lines: List[str] = []
    for c in list_characters(project_id):
        name = (c.get("name") or "").strip()
        if not name or (name not in blob and f"@{name}" not in blob):
            continue
        vp = (c.get("visual_prompt") or "").strip().replace("\n", " ")[:220]
        vd = (c.get("voice_desc") or "").strip().replace("\n", " ")[:100]
        tv = (c.get("tts_voice") or "").strip()
        bit = f"@{name}"
        if vp:
            bit += f"：外形 {vp}"
        if vd:
            bit += f"；音色 {vd}"
        if tv:
            bit += f"；TTS:{tv}"
        char_lines.append(bit)
    if char_lines:
        lines.append("【资产库锁定·人物】必须与下列外形/音色一致，禁止换脸换声：")
        lines.extend(char_lines[:16])
    scene_lines: List[str] = []
    for sc in list_scenes(project_id):
        name = (sc.get("name") or "").strip()
        code = (sc.get("code") or "").strip()
        keys = [x for x in (name, code) if x]
        if not keys or not any(k in blob or f"@{k}" in blob for k in keys):
            # 母版名常见于 @偏院室内母版
            vp0 = (sc.get("visual_prompt") or "").strip()
            if not any(f"@{k}" in blob for k in keys):
                if name and name not in blob and (not code or code not in blob):
                    continue
        tag = name or code
        vp = (sc.get("visual_prompt") or sc.get("role_note") or "").strip().replace("\n", " ")[:220]
        if tag and vp:
            scene_lines.append(f"@{tag}：{vp}")
        elif tag:
            scene_lines.append(f"@{tag}")
    if scene_lines:
        lines.append("【资产库锁定·场景】空间母版与陈设必须一致：")
        lines.extend(scene_lines[:10])
    prop_lines: List[str] = []
    for p in list_props(project_id):
        name = (p.get("name") or "").strip()
        if not name or (name not in blob and f"@{name}" not in blob):
            continue
        vp = (p.get("visual_prompt") or p.get("role_note") or "").strip().replace("\n", " ")[:160]
        prop_lines.append(f"@{name}：{vp}" if vp else f"@{name}")
    if prop_lines:
        lines.append("【资产库锁定·道具】外形必须一致：")
        lines.extend(prop_lines[:12])
    return "\n".join(lines).strip()


def _build_drama10_prompt(
    project_id: str,
    episode_id: str,
    shot_nums: Optional[List[int]] = None,
    *,
    for_video: bool = False,
) -> str:
    ep = get_episode(episode_id)
    proj = get_project(project_id)
    prefs = get_generation_prefs(project_id)
    proj_style = proj.get("style_prefix") or ""
    _, neg = effective_style(proj_style, prefs)
    payload: Dict[str, Any] = {
        "episode": ep["ep_num"],
        "title": ep.get("title") or "",
        "style_prefix": proj_style,
        "shots": [],
    }
    from .asset_recovery import is_bingjing_project_id

    leg_ep: Dict[str, Any] = {}
    # 仅冰镜可读共享 web/data/epXX.json；其它短剧只用本项目字段，避免串戏
    if is_bingjing_project_id(project_id):
        try:
            from .drama10_export import load_episode as _load_ep_json

            leg_ep = _load_ep_json(int(ep["ep_num"]))
        except (OSError, ValueError, TypeError, FileNotFoundError):
            leg_ep = {}
    else:
        script_path = (
            db.project_dir(project_id) / "scripts" / f"ep{int(ep['ep_num']):02d}.json"
        )
        if script_path.is_file():
            try:
                raw = json.loads(script_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    leg_ep = raw
            except (OSError, json.JSONDecodeError, UnicodeError):
                leg_ep = {}
    for s in ep.get("shots") or []:
        sn = int(s["shot_num"])
        if s.get("voices") is not None:
            voices = s.get("voices") or []
        else:
            try:
                voices = json.loads(s.get("voices_json") or "[]")
            except json.JSONDecodeError:
                voices = []
        vis_raw = s.get("visual") or ""
        est = _is_establishing_shot(vis_raw)
        voices = _enrich_voices_from_assets(project_id, list(voices or []))
        payload["shots"].append(
            {
                "id": sn,
                "title": (s.get("title") or f"镜{sn}").strip(),
                "duration_sec": s["duration_sec"],
                "style_prefix": style_prefix_for_shot(vis_raw, proj_style, prefs),
                "visual": apply_negative_to_visual(vis_raw, neg, establishing=est),
                "motion": (s.get("motion") or "").strip(),
                "voices": voices,
                "ambience": (s.get("ambience") or "").strip(),
                "scene": (s.get("scene") or s.get("visual") or "").strip(),
                "camera": (s.get("camera") or "").strip(),
                "performance": (s.get("performance") or "").strip(),
                "continuity": (s.get("continuity") or "").strip(),
            }
        )
    docs = proj.get("docs") or {}
    payload["synopsis"] = (leg_ep.get("synopsis") or "").strip()
    asset_bible = _asset_lock_bible(project_id, payload["shots"])
    legacy_bible = (
        (leg_ep.get("character_bible") or "").strip()
        or (docs.get("series_scripts_note") or "").strip()
        or (prefs.get("global_memory") or "").strip()
    )
    if asset_bible and legacy_bible and legacy_bible not in ("SCRIPTSET 导入",):
        payload["character_bible"] = asset_bible + "\n" + legacy_bible[:800]
    elif asset_bible:
        payload["character_bible"] = asset_bible
    else:
        payload["character_bible"] = legacy_bible
    payload["visual_style"] = (
        leg_ep.get("visual_style") or leg_ep.get("style_prefix") or proj_style
    ).strip()
    if shot_nums:
        want = {int(x) for x in shot_nums}
        payload["shots"] = [s for s in payload["shots"] if int(s["id"]) in want]
        if not payload["shots"]:
            raise ValueError(f"找不到镜头：{sorted(want)}")
    prompt = episode_to_drama10(payload, for_video=for_video)
    return prompt


def _episode_drama10_text(project_id: str, episode_id: str) -> str:
    return _build_drama10_prompt(project_id, episode_id)


def shot_storyboard_regen_preview(
    project_id: str, episode_id: str, shot_num: int
) -> Dict[str, Any]:
    """单镜重生前预览：画面、Drama10 块、Comfy 出图提示词。"""
    sn = int(shot_num)
    ep = get_episode(episode_id)
    shot = next((s for s in ep.get("shots") or [] if int(s["shot_num"]) == sn), None)
    if not shot:
        raise KeyError(f"镜 {sn} 不存在")
    full = _build_drama10_prompt(project_id, episode_id)
    block = filter_prompt_shots(full, [sn])
    return {
        "shot_num": sn,
        "duration_sec": float(shot.get("duration_sec") or 5),
        "visual": shot.get("visual") or "",
        "drama10_block": block,
        "still_prompt": still_prompt_for_shot(full, sn),
    }


def _apply_shot_regen_overrides(episode_id: str, params: Dict[str, Any]) -> None:
    visuals = params.get("shot_visuals") or {}
    for key, visual in visuals.items():
        with db.connect() as conn:
            conn.execute(
                "UPDATE shots SET visual=? WHERE episode_id=? AND shot_num=?",
                (str(visual), episode_id, int(key)),
            )


def reprocess_episode_storyboard_tiles(project_id: str, episode_id: str) -> Dict[str, Any]:
    """对已有单镜分镜图重新套用方向校正（无需重跑 Comfy）。"""
    ep = get_episode(episode_id)
    proj = get_project(project_id)
    prefs = get_generation_prefs(project_id)
    n = len(ep.get("shots") or [])
    if n < 1:
        raise ValueError("本集无镜头")
    w, h = resolve_still_canvas(proj, prefs)
    reprocess_storyboard_episode(project_id, int(ep["ep_num"]), n, w, h)
    return {"ep_num": ep["ep_num"], "shots": n, "rotate_deg": storyboard_tile_rotate_degrees()}


def fix_episode_shot_orientation(
    project_id: str, episode_id: str, shot_num: Optional[int] = None
) -> Dict[str, Any]:
    ep = get_episode(episode_id)
    proj = get_project(project_id)
    prefs = get_generation_prefs(project_id)
    w, h = resolve_still_canvas(proj, prefs)
    ep_tag = f"ep{ep['ep_num']:02d}"
    shots_dir = db.project_dir(project_id) / "episodes" / ep_tag / "shots"
    nums = [int(shot_num)] if shot_num else [int(s["shot_num"]) for s in ep.get("shots") or []]
    fixed = []
    manual = shot_num is not None
    for sn in nums:
        path = shots_dir / f"shot_{sn:02d}.png"
        if not path.is_file():
            continue
        ok = (
            manual_rotate_portrait_shot(path, w, h)
            if manual
            else finalize_storyboard_tile(path, w, h)
        )
        if ok:
            fixed.append(sn)
    n = len(ep.get("shots") or [])
    return {"fixed_shots": fixed, "checked": nums}


def _job_drama10_export(jid: str, project_id: str, episode_id: str) -> None:
    _job_update(jid, "running", 0.3, "组装 Drama10 Prompt…")
    ep = get_episode(episode_id)
    proj = get_project(project_id)
    payload = {
        "episode": ep["ep_num"],
        "title": ep.get("title") or "",
        "style_prefix": proj.get("style_prefix") or "",
        "shots": [],
    }
    for s in ep.get("shots") or []:
        voices = json.loads(s.get("voices_json") or "[]")
        payload["shots"].append(
            {
                "id": s["shot_num"],
                "title": f"镜{s['shot_num']}",
                "duration_sec": s["duration_sec"],
                "visual": s["visual"],
                "voices": voices,
            }
        )
    if not payload["shots"] and ep.get("storyboard_md"):
        payload["shots"] = [{"id": 1, "visual": ep["storyboard_md"][:200], "voices": []}]
    prompt = _build_drama10_prompt(project_id, episode_id)
    params = drama10_params(
        payload, proj["width"], proj["height"], None
    )
    fname = save_drama10_export_file(project_id, int(ep["ep_num"]), prompt)
    update_episode(
        episode_id,
        drama10_prompt=prompt,
        pipeline_json={**json.loads(ep.get("pipeline_json") or "{}"), "storyboard_script": "done"},
    )
    _job_update(
        jid,
        "done",
        1.0,
        f"已导出 Drama10（{params['n_shots']} 镜）→ {fname.name}",
    )


def _fmt_job_clock(seconds: float) -> str:
    """与 Comfy tqdm 类似的 mm:ss（超过 1 小时用 h:mm:ss）。"""
    s = int(max(0, float(seconds or 0)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _comfy_wait_tick(
    jid: str, base_progress: float, span: float, phase: str = ""
):
    """Comfy wait 轮询时更新进度；优先展示 Comfy 日志 tqdm 剩余时长。"""

    def _tick(elapsed: float, q: Dict[str, Any]) -> None:
        step = int(q.get("step") or 0)
        steps = int(q.get("steps") or 0)
        if steps > 0:
            bump = span * (0.08 + 0.77 * min(1.0, step / float(steps)))
        else:
            bump = min(span * 0.85, elapsed / 600.0 * span)
        run_q = int(q.get("running") or 0)
        pend_q = int(q.get("pending") or 0)
        head = phase or "ComfyUI 处理中"
        parts = [head, f"已用 {_fmt_job_clock(elapsed)}"]

        eta: Optional[float] = None
        eta_source = (q.get("eta_source") or "").strip()
        # 1) Comfy 日志 tqdm：2/6 [02:53<05:47, …]
        log_eta = q.get("log_eta_s")
        if log_eta is None and eta_source == "comfy_log":
            log_eta = q.get("eta_s")
        if log_eta is not None:
            try:
                eta = max(0.0, float(log_eta))
                eta_source = "comfy_log"
            except (TypeError, ValueError):
                eta = None
        # 2) WebSocket 步时估算
        if eta is None:
            raw_eta = q.get("eta_s")
            if raw_eta is not None:
                try:
                    eta = max(0.0, float(raw_eta))
                    eta_source = eta_source or "ws"
                except (TypeError, ValueError):
                    eta = None
        # 3) 本地步数推算 / 经验兜底（仅无日志时）
        if eta is None and steps > 0 and step > 0:
            eta = max(0.0, (steps - step) * (elapsed / float(step)))
            eta_source = "steps"
        if eta is None:
            typical = float(os.environ.get("DRAMA10_SHOT_ETA_SEC", "520") or 520)
            typical = max(120.0, min(2400.0, typical))
            eta = max(15.0, typical - float(elapsed))
            if pend_q > 0:
                eta += typical * 0.45 * min(3, pend_q)
            eta_source = "estimate"

        raw_remain = (q.get("log_remain") or "").strip()
        if eta_source == "comfy_log":
            # 与终端一致：优先原样显示日志里的 <剩余>
            clock = raw_remain if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", raw_remain) else _fmt_job_clock(eta)
            parts.append(f"Comfy剩余 {clock}")
        else:
            parts.append(f"剩余约 {_fmt_job_clock(eta)}")

        if steps > 0:
            pct = int(q.get("step_pct") or int(100 * step / steps))
            label = "Comfy采样" if eta_source == "comfy_log" else "采样"
            parts.append(f"{label} {step}/{steps}（{pct}%）")
        if run_q or pend_q:
            parts.append(f"队列 执行={run_q} 等待={pend_q}")
        _job_update(jid, "running", base_progress + bump, " · ".join(parts))

    return _tick


def _storyboard_negative_prompt(project_id: str, visual: str = "") -> str:
    from .generation_prefs import ESTABLISHING_NEGATIVE, _is_establishing_shot

    proj = get_project(project_id)
    prefs = get_generation_prefs(project_id)
    _, neg = effective_style(proj.get("style_prefix") or "", prefs)
    if _is_establishing_shot(visual or ""):
        return f"{neg}，{ESTABLISHING_NEGATIVE}"
    return neg


def _storyboard_steps_for_prompt(prompt: str) -> int:
    base = int(os.environ.get("STORYBOARD_STEPS", "14"))
    if _needs_face_hint(prompt):
        return int(os.environ.get("STORYBOARD_CLOSEUP_STEPS", str(max(base, 18))))
    return base


def _job_storyboard_images(
    jid: str, project_id: str, episode_id: str, params: Optional[Dict[str, Any]] = None
) -> None:
    params = params or {}
    shot_nums: Optional[List[int]] = params.get("shot_nums")
    if shot_nums:
        shot_nums = [int(x) for x in shot_nums]

    ep = get_episode(episode_id)
    proj = get_project(project_id)
    shots = ep.get("shots") or []
    if not shots:
        raise ValueError("本集无镜头，请先填写镜头表或导出 Drama10")

    n = len(shots)
    cols, rows = compute_grid(n)
    ep_tag = f"ep{ep['ep_num']:02d}"
    ep_dir = db.project_dir(project_id) / "episodes" / ep_tag
    shots_dir = ep_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    proj_base = db.project_dir(project_id)
    prefs = get_generation_prefs(project_id)
    w, h = resolve_still_canvas(proj, prefs)
    sb_backend = resolve_storyboard_backend(prefs)
    if (params.get("storyboard_backend") or "").strip() in (
        BACKEND_MINIMAX,
        BACKEND_SDXL,
        BACKEND_IMAGE_API,
    ):
        sb_backend = str(params["storyboard_backend"]).strip()
    # 配置了 API 出图但未存 Key 时，禁止静默回退到已断开的 Comfy（否则只显示 timed out）
    wanted = (prefs.get("storyboard_backend") or params.get("storyboard_backend") or "").strip()
    if wanted == BACKEND_IMAGE_API and not image_api_configured():
        raise RuntimeError(
            "分镜引擎为「第三方 API」，但未配置 OhMyGPT Key。"
            "请在「全局制作 → OhMyGPT 密钥」保存后重试（勿依赖已断开的本机 Comfy）。"
        )
    sb_negative = _storyboard_negative_prompt(project_id)
    skip_h3_rotate = sb_backend in (BACKEND_SDXL, BACKEND_IMAGE_API)

    shot_by_num = {int(s["shot_num"]): s for s in shots}
    char_bible = (ep.get("character_bible") or "").strip()

    def _save_one_shot_png(src: Path, shot_num: int) -> None:
        dest = shots_dir / f"shot_{shot_num:02d}.png"
        copy_into_project(src, dest)
        finalize_storyboard_tile(dest, w, h, skip_rotation=skip_h3_rotate)
        vis_raw = (shot_by_num.get(shot_num) or {}).get("visual") or ""
        if not _is_black_shot(vis_raw):
            validate_still_not_near_black(dest)
        rel = str(dest.relative_to(proj_base)).replace("\\", "/")
        stale_mp4 = shots_dir / f"shot_{shot_num:02d}.mp4"
        if stale_mp4.is_file() and stale_mp4.stat().st_mtime < dest.stat().st_mtime:
            try:
                stale_mp4.unlink()
            except OSError:
                pass
        with db.connect() as conn:
            conn.execute(
                "UPDATE shots SET image_path=?, video_path=? WHERE episode_id=? AND shot_num=?",
                (rel, "", episode_id, shot_num),
            )

    def _comfy_generate_stills(nums: List[int], still_overrides: Dict[str, Any]) -> None:
        full_drama10 = _build_drama10_prompt(project_id, episode_id)
        backend_label = "SDXL 文生图" if sb_backend == BACKEND_SDXL else "MiniMax H3 静帧"
        for idx, sn in enumerate(nums):
            still_prompt = still_overrides.get(str(sn)) or still_overrides.get(sn)
            if not still_prompt:
                vis0 = (shot_by_num.get(sn) or {}).get("visual") or ""
                still_prompt = still_prompt_for_shot(
                    full_drama10,
                    sn,
                    visual=vis0,
                    character_bible=char_bible,
                )
            else:
                still_prompt = str(still_prompt).strip()
            prefix = f"bingjing/{project_id[:8]}/{ep_tag}/shot_{sn:02d}"
            base_p = 0.15 + (idx / max(len(nums), 1)) * 0.75
            _job_update(
                jid,
                "running",
                base_p,
                f"ComfyUI {backend_label} · 镜{sn}（{idx + 1}/{len(nums)}）…",
            )
            graph = build_single_shot_still_workflow(
                still_prompt,
                w,
                h,
                negative=sb_negative,
                noise_seed=42 + int(ep["ep_num"]) * 100 + sn,
                steps=_storyboard_steps_for_prompt(still_prompt),
                filename_prefix=prefix,
                backend=sb_backend,
                prefs=prefs,
            )
            pid = client.queue_prompt(graph)
            hist = client.wait_prompt(
                pid, timeout=7200.0, on_tick=_comfy_wait_tick(jid, base_p, 0.06)
            )
            imgs, _ = resolve_output_files(hist, client=client)
            src = pick_first_saved_still(imgs) or find_newest_under(prefix, ".png")
            if src is None or not src.is_file():
                raise FileNotFoundError(f"镜{sn} 无 ComfyUI 输出")
            _save_one_shot_png(src, sn)

    def _api_generate_stills(nums: List[int], still_overrides: Dict[str, Any]) -> None:
        if not image_api_configured():
            raise RuntimeError(
                "未配置第三方出图：请在服务器设置 STORYBOARD_IMAGE_API_BASE 与 STORYBOARD_IMAGE_API_KEY"
            )
        full_drama10 = _build_drama10_prompt(project_id, episode_id)
        model = image_api_model(prefs)
        def _black_still_temp() -> Path:
            from PIL import Image

            fd, name = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            path = Path(name)
            Image.new("RGB", (max(w, 64), max(h, 64)), (0, 0, 0)).save(path)
            return path

        for idx, sn in enumerate(nums):
            still_prompt = still_overrides.get(str(sn)) or still_overrides.get(sn)
            vis_raw = (shot_by_num.get(sn) or {}).get("visual") or ""
            if not still_prompt:
                still_prompt = still_prompt_for_shot(
                    full_drama10,
                    sn,
                    visual=vis_raw,
                    character_bible=char_bible,
                )
            else:
                still_prompt = str(still_prompt).strip()
            base_p = 0.15 + (idx / max(len(nums), 1)) * 0.75
            _job_update(
                jid,
                "running",
                base_p,
                f"API 出图 {model} · 镜{sn}（{idx + 1}/{len(nums)}）· 等待上游返回…",
            )
            shot_neg = _storyboard_negative_prompt(project_id, vis_raw)
            if _is_establishing_shot(vis_raw):
                still_prompt = (
                    "【硬性空镜】仅环境建筑与雪景，禁止人物特写。 "
                    + still_prompt
                )
            if _is_black_shot(still_prompt) or _is_black_shot(vis_raw):
                tmp = _black_still_temp()
            else:
                try:
                    tmp = generate_still_via_api_temp(
                        still_prompt,
                        shot_neg,
                        w,
                        h,
                        model=model,
                    )
                except Exception as exc:
                    raise RuntimeError(f"镜{sn} 出图失败：{exc}") from exc
            try:
                _save_one_shot_png(tmp, sn)
            finally:
                tmp.unlink(missing_ok=True)
            # 每镜落盘后立刻抬进度，前端轮询即可看到缩略图（不必等整集）
            done_p = 0.15 + ((idx + 1) / max(len(nums), 1)) * 0.75
            _job_update(
                jid,
                "running",
                min(0.92, done_p),
                f"已完成镜{sn}（{idx + 1}/{len(nums)}），继续下一镜…",
            )

    still_overrides = dict(params.get("still_prompts") or {})
    for k, v in still_prompt_overrides_for_project(project_id, int(ep["ep_num"])).items():
        still_overrides.setdefault(str(k), v)

    ep_num_i = int(ep["ep_num"])
    # 整集批量默认跳过已有 PNG；单镜 shot_nums / fresh_images 强制重出
    skip_existing_stills = (
        not shot_nums
        and not bool(params.get("fresh_images"))
        and bool(params.get("skip_existing", True))
    )
    if shot_nums:
        target_nums = list(shot_nums)
    else:
        target_nums = [int(s["shot_num"]) for s in shots]
        if skip_existing_stills:
            missing = [
                sn
                for sn in target_nums
                if not _shot_asset_ready(_shot_still_png_path(project_id, ep_num_i, sn))
            ]
            skipped = [sn for sn in target_nums if sn not in missing]
            if skipped:
                _job_update(
                    jid,
                    "running",
                    0.08,
                    f"已有分镜图跳过：镜 {skipped}；待补 {len(missing)} 镜",
                )
            if not missing:
                try:
                    pl = json.loads(ep.get("pipeline_json") or "{}")
                except json.JSONDecodeError:
                    pl = _default_episode_pipeline()
                pl["storyboard_images"] = "done"
                update_episode(episode_id, pipeline_json=pl)
                _job_update(
                    jid,
                    "done",
                    1.0,
                    f"全部 {n} 镜分镜图已存在，已跳过出图",
                )
                return
            target_nums = missing

    if sb_backend == BACKEND_IMAGE_API:
        if shot_nums:
            _job_update(jid, "running", 0.12, f"单镜重生成：{shot_nums}（API）")
            _apply_shot_regen_overrides(episode_id, params)
        else:
            _job_update(
                jid,
                "running",
                0.12,
                f"批量补缺分镜图 · API（待出 {len(target_nums)}/{n} 镜）…",
            )
        _api_generate_stills(target_nums, still_overrides)
    else:
        with COMFY_QUEUE_LOCK:
            client = ComfyClient()
            client.ping()
            if shot_nums:
                _job_update(jid, "running", 0.12, f"单镜重生成：{shot_nums}（{sb_backend}）")
                _apply_shot_regen_overrides(episode_id, params)
            else:
                _job_update(
                    jid,
                    "running",
                    0.12,
                    f"批量补缺分镜图 · {sb_backend}（待出 {len(target_nums)}/{n} 镜）…",
                )
            _comfy_generate_stills(target_nums, still_overrides)

    try:
        pl = json.loads(ep.get("pipeline_json") or "{}")
    except json.JSONDecodeError:
        pl = _default_episode_pipeline()
    pl["storyboard_images"] = "done"
    final_prompt = _build_drama10_prompt(project_id, episode_id)
    save_drama10_export_file(project_id, int(ep["ep_num"]), final_prompt)
    update_episode(
        episode_id,
        drama10_prompt=final_prompt,
        pipeline_json=pl,
    )
    if shot_nums:
        _job_update(jid, "done", 1.0, f"已重生成镜头 {shot_nums}")
    else:
        _job_update(
            jid,
            "done",
            1.0,
            f"分镜出图完成（新出 {len(target_nums)} 镜 / 共 {n} 镜）",
        )


MAX_VIDEO_COMFY_ATTEMPTS = 12


def _comfy_video_retriable(exc: BaseException) -> bool:
    mod = getattr(type(exc), "__module__", "") or ""
    if "httpx" in mod and type(exc).__name__ in (
        "ConnectError",
        "ReadError",
        "RemoteProtocolError",
        "TimeoutException",
    ):
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    msg = str(exc).lower()
    needles = (
        "connection refused",
        "connection reset",
        "comfyui 不可达",
        "comfyui 任务已从队列消失",
        "checkpoint 续跑",
        "errno 111",
        "errno 104",
    )
    return any(n in msg for n in needles)


def _wait_comfy_ready(max_sec: float = 900.0) -> ComfyClient:
    deadline = time.time() + max_sec
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            client = ComfyClient()
            client.ping()
            return client
        except Exception as e:
            last_err = e
            time.sleep(20)
    raise RuntimeError(f"ComfyUI 在 {int(max_sec)}s 内未恢复: {last_err}")


def _episode_video_use_segments() -> bool:
    v = os.environ.get("EPISODE_VIDEO_SEGMENTED", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _episode_video_restart_comfy_between_shots() -> bool:
    """镜间重启 Comfy 易误判断线且占内存；默认关，仅 OOM 后由 systemd 拉起。"""
    v = os.environ.get("EPISODE_VIDEO_RESTART_COMFY", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _episode_video_resume_ckpt() -> bool:
    v = os.environ.get("EPISODE_VIDEO_RESUME", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _delete_comfy_shots_from_latest_ckpt(shot_nums: list[int]) -> int:
    """仅删除指定镜号的 pt/mp4，便于重跑单镜后 resume 拼接。"""
    removed = 0
    ckpt_root = comfy_root() / "output" / "drama_ui" / "ckpt"
    if not ckpt_root.is_dir():
        return 0
    dirs = sorted(
        [d for d in ckpt_root.iterdir() if d.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not dirs:
        return 0
    ckpt = dirs[0]
    mp4_dir = ckpt / "mp4"
    for sn in shot_nums:
        for p in (ckpt / f"shot_{int(sn):02d}.pt", mp4_dir / f"shot_{int(sn):02d}.mp4"):
            if p.is_file():
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
    for name in ("audio.pt", "video_silent.mp4", "audio.wav", "DONE"):
        p = ckpt / name
        if p.is_file():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _purge_comfy_drama_ckpt_cache() -> int:
    """清空 Drama10 checkpoint（shot_*.pt / mp4 / 音轨 / source_sig），避免旧 i2v 被复用。"""
    removed = 0
    ckpt_root = comfy_root() / "output" / "drama_ui" / "ckpt"
    if ckpt_root.is_dir():
        for ckpt in ckpt_root.iterdir():
            if not ckpt.is_dir():
                continue
            for pt in ckpt.glob("shot_*.pt"):
                try:
                    pt.unlink()
                    removed += 1
                except OSError:
                    pass
            mp4_dir = ckpt / "mp4"
            if mp4_dir.is_dir():
                for mp4 in mp4_dir.glob("*.mp4"):
                    try:
                        mp4.unlink()
                        removed += 1
                    except OSError:
                        pass
            for name in (
                "audio.pt",
                "source_sig.json",
                "video_silent.mp4",
                "audio.wav",
                "DONE",
                "_shot_stills_sig.md5",
            ):
                p = ckpt / name
                if p.is_file():
                    try:
                        p.unlink()
                        removed += 1
                    except OSError:
                        pass
            for p in ckpt.glob("_xfade_step_*.mp4"):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
    tmp_root = Path("/tmp")
    if tmp_root.is_dir():
        for d in tmp_root.glob("drama10_shots_*"):
            if d.is_dir():
                try:
                    import shutil

                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
                except OSError:
                    pass
    return removed


def _sync_episode_shot_stills_to_comfy(
    project_id: str, ep_tag: str, n_shots: int
) -> Optional[str]:
    """将 project/episodes/…/shots/shot_XX.png 同步到 Comfy input，供 Drama10 逐镜 i2v。

    本机 Comfy：直接写 COMFYUI_ROOT/input；云 GPU（隧道）额外走 /upload/image。
    """
    shots_dir = db.project_dir(project_id) / "episodes" / ep_tag / "shots"
    subdir = f"bingjing/{project_id[:8]}_{ep_tag}_shots"
    dest = comfy_root() / "input" / subdir
    dest.mkdir(parents=True, exist_ok=True)
    synced = 0
    client: Optional[ComfyClient] = None
    for i in range(1, max(n_shots, 1) + 1):
        src = shots_dir / f"shot_{i:02d}.png"
        if not src.is_file():
            continue
        dst = dest / f"shot_{i:02d}.png"
        need_write = (
            not dst.is_file()
            or src.stat().st_size != dst.stat().st_size
            or int(src.stat().st_mtime) > int(dst.stat().st_mtime) + 1
        )
        if need_write:
            dst.write_bytes(src.read_bytes())
        # 远端 Comfy 没有共享磁盘时，始终上传（overwrite）保证 H3 input 有图
        try:
            if client is None:
                client = ComfyClient()
            client.upload_image(src, subfolder=subdir, overwrite=True, image_type="input")
        except Exception as exc:
            # 本机 Comfy 已落盘时允许上传失败；纯远端时后面会因缺图失败
            if not dst.is_file():
                raise RuntimeError(f"同步分镜到 Comfy 失败 shot_{i:02d}: {exc}") from exc
        synced += 1
    return subdir if synced else None


def _restart_comfy_user_service() -> None:
    """Release RAM/VRAM between segmented shots (skip if Comfy queue still busy)."""
    try:
        client = ComfyClient()
        q = client.get_queue()
        if (q.get("queue_running") or []) or (q.get("queue_pending") or []):
            return
    except Exception:
        pass
    subprocess.run(
        ["systemctl", "--user", "restart", "bingjing-comfy.service"],
        capture_output=True,
        timeout=90,
    )
    time.sleep(8)
    _wait_comfy_ready(480.0)


def _comfy_episode_video_pass(
    jid: str,
    *,
    prompt: str,
    vid_w: int,
    vid_h: int,
    params: Dict[str, Any],
    ep_num: int,
    auto_sb: bool,
    collage_rel: Optional[str],
    shot_stills_subdir: Optional[str],
    vid_prefix: str,
    sb_prefix: str,
    single_shot_index: int = 0,
    finalize_mux: bool = True,
    progress_base: float = 0.22,
    progress_span: float = 0.68,
    wait_timeout: float = 2400.0,
    pass_label: str = "",
    resume_ckpt: Optional[bool] = None,
) -> Dict[str, Any]:
    label = pass_label or ("拼接" if finalize_mux else f"镜{single_shot_index}")
    hist: Optional[Dict[str, Any]] = None
    for attempt in range(1, MAX_VIDEO_COMFY_ATTEMPTS + 1):
        if not _job_still_running(jid):
            return {}
        try:
            with COMFY_QUEUE_LOCK:
                wait_cap = 900.0 if attempt == 1 else 600.0
                _job_update(
                    jid,
                    "running",
                    progress_base,
                    f"{label} · 连接 Comfy（{attempt}/{MAX_VIDEO_COMFY_ATTEMPTS}）…",
                )
                client = _wait_comfy_ready(wait_cap)
                # 一律要求 Drama10 H3 原生音轨，禁止静音预览
                preview_audio = True
                if "preview_with_audio" in params:
                    preview_audio = bool(params.get("preview_with_audio"))
                graph = build_episode_video_workflow(
                    prompt,
                    vid_w,
                    vid_h,
                    shot_duration=float(params["shot_duration"]),
                    noise_seed=42 + int(ep_num),
                    steps=int(params.get("steps") or 8),
                    grid_cols=int(params["grid_cols"]),
                    grid_rows=int(params["grid_rows"]),
                    auto_storyboard=auto_sb,
                    resume=(
                        _episode_video_resume_ckpt()
                        if resume_ckpt is None
                        else bool(resume_ckpt)
                    ),
                    collage_image=collage_rel,
                    shot_stills_subdir=shot_stills_subdir,
                    video_prefix=vid_prefix,
                    storyboard_prefix=sb_prefix,
                    single_shot_index=single_shot_index,
                    finalize_mux=finalize_mux,
                    preview_with_audio=bool(preview_audio),
                )
                pid = client.queue_prompt(graph)
                _job_update(
                    jid,
                    "running",
                    progress_base + progress_span * 0.05,
                    f"{label} · 已入队 {pid[:8]}…",
                )
                hist = client.wait_prompt(
                    pid,
                    timeout=wait_timeout,
                    on_tick=_comfy_wait_tick(
                        jid, progress_base, progress_span * 0.95, phase=label
                    ),
                )
            return hist
        except Exception as e:
            if not _comfy_video_retriable(e) or attempt >= MAX_VIDEO_COMFY_ATTEMPTS:
                raise
            short = str(e).replace("\n", " ")[:120]
            _job_update(
                jid,
                "running",
                progress_base,
                f"{label} · Comfy 中断：{short} · 续跑 {attempt}/{MAX_VIDEO_COMFY_ATTEMPTS}…",
            )
            time.sleep(25)
    raise RuntimeError(f"{label} 未获得 ComfyUI 输出")


def _strip_mp4_audio_inplace(mp4: Path) -> None:
    """去掉 mp4 音轨（官方 H3「仅无声画面」）。"""
    mp4 = Path(mp4)
    if not mp4.is_file():
        return
    tmp = mp4.with_suffix(".mute.tmp.mp4")
    try:
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mp4),
                "-an",
                "-c:v",
                "copy",
                "-movflags",
                "+faststart",
                str(tmp),
            ],
            timeout=120,
        )
        if tmp.is_file() and tmp.stat().st_size > 256:
            tmp.replace(mp4)
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.SubprocessError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"去除音轨失败：{e}") from e


def _ffmpeg_concat_shot_mp4s(clips: List[Path], out_mp4: Path) -> None:
    """无损拼接单镜 mp4 → 整集成片。"""
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    lst = out_mp4.parent / f".concat_{out_mp4.stem}.txt"
    with lst.open("w", encoding="utf-8") as f:
        for c in clips:
            # ffmpeg concat demuxer：单引号路径
            p = str(Path(c).resolve()).replace("'", "'\\''")
            f.write(f"file '{p}'\n")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lst),
        "-c",
        "copy",
        str(out_mp4),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        lst.unlink(missing_ok=True)  # type: ignore[call-arg]
    except TypeError:
        if lst.is_file():
            lst.unlink()


def _job_episode_video_minimax(
    jid: str,
    project_id: str,
    episode_id: str,
    job_params: Optional[Dict[str, Any]] = None,
) -> None:
    """官方 MiniMax H3：逐镜图生视频 → 拼接成片（音轨由 H3 原生带出）。"""
    from . import minimax_official as mm

    if not _job_still_running(jid):
        return
    job_params = job_params or {}
    if not mm.ready():
        raise RuntimeError(
            "未配置 MiniMax API Key。请在「全局制作 → 视频生成后端」选择官方 API 并保存密钥。"
        )

    ep = get_episode(episode_id)
    shots = ep.get("shots") or []
    ep_num = int(ep["ep_num"])
    ep_tag = f"ep{ep_num:02d}"
    ep_dir = db.project_dir(project_id) / "episodes" / ep_tag
    shots_dir = ep_dir / "shots"
    mux_only = bool(job_params.get("mux_only"))

    only_shots: Optional[List[int]] = None
    _raw_sn = job_params.get("shot_nums")
    if _raw_sn:
        only_shots = sorted({int(x) for x in _raw_sn})

    video_shot_nums = [
        int(s["shot_num"])
        for s in shots
        if not _is_black_shot((s.get("visual") or ""))
    ]
    if not video_shot_nums:
        video_shot_nums = [int(s["shot_num"]) for s in shots]
    if only_shots:
        filtered = [sn for sn in video_shot_nums if sn in only_shots]
        video_shot_nums = filtered if filtered else list(only_shots)

    rerun_raw = job_params.get("rerun_shot_nums") or job_params.get("rerun_shots")
    if rerun_raw and not mux_only:
        video_shot_nums = sorted({int(x) for x in rerun_raw})

    # 整集批量默认跳过已有 mp4；单镜 / fresh_video / rerun 强制重出
    skip_existing_videos = (
        not mux_only
        and not only_shots
        and not rerun_raw
        and not bool(job_params.get("fresh_video"))
        and bool(job_params.get("skip_existing", True))
    )
    generate_nums = list(video_shot_nums)
    if skip_existing_videos:
        missing = [
            sn
            for sn in generate_nums
            if not _shot_asset_ready(_shot_video_mp4_path(project_id, ep_num, sn))
        ]
        kept = [sn for sn in generate_nums if sn not in missing]
        if kept:
            _job_update(
                jid,
                "running",
                0.06,
                f"已有分镜视频跳过：镜 {kept}；待补 {len(missing)} 镜",
            )
        generate_nums = missing

    # 默认不自动拼接：批量只出分镜视频，合并用 mux_only / auto_concat
    auto_concat = bool(job_params.get("auto_concat")) or mux_only
    if only_shots:
        auto_concat = False

    if not mux_only:
        if not generate_nums and not auto_concat:
            _reconcile_shot_videos_from_disk(project_id, episode_id, ep_num, shots)
            _job_update(
                jid,
                "done",
                1.0,
                f"全部待做镜已有视频，已跳过生成；请点「合并成片」拼接",
            )
            return
        if generate_nums:
            _assert_shot_stills_for_video(
                project_id,
                episode_id,
                shot_nums=generate_nums,
                mux_only=False,
            )

    prompt = _build_drama10_prompt(
        project_id,
        episode_id,
        shot_nums=generate_nums if (only_shots or rerun_raw or skip_existing_videos) else None,
        for_video=True,
    )
    if not mux_only and generate_nums and not prompt.strip():
        raise ValueError("无 Drama10 脚本，请先生成 Prompt 或填写镜头")
    if prompt.strip():
        save_drama10_export_file(project_id, ep_num, prompt)

    with_audio = bool(job_params.get("preview_with_audio", True))
    shot_by_num = {int(s["shot_num"]): s for s in shots}

    if not mux_only and generate_nums:
        n_vid = len(generate_nums)
        seg = 0.7 / max(n_vid, 1)
        for i, sn in enumerate(generate_nums):
            if not _job_still_running(jid):
                return
            base = 0.12 + i * seg
            still = _shot_still_png_path(project_id, ep_num, sn)
            dest = shots_dir / f"shot_{sn:02d}.mp4"
            shot_row = shot_by_num.get(sn) or {}
            try:
                block = filter_prompt_shots(prompt, [sn])
            except ValueError:
                # Drama10 块缺失时，用镜头表 visual/台词拼一段可用提示，避免整集漏镜
                voices = shot_row.get("voices") or []
                lines = []
                for v in voices:
                    if isinstance(v, dict) and (v.get("text") or "").strip():
                        lines.append(f"{v.get('role') or '旁白'}：{(v.get('text') or '').strip()}")
                block = (
                    f"镜头 {sn}\n"
                    f"画面：{(shot_row.get('visual') or shot_row.get('title') or '').strip()}\n"
                    f"运镜：{(shot_row.get('motion') or '缓慢推进').strip()}\n"
                    + ("台词：\n" + "\n".join(lines) if lines else "")
                ).strip() or prompt
            dur = float(shot_row.get("duration_sec") or 6)
            if only_shots:
                label = f"单段-第{ep_num}集镜{sn}"
            else:
                label = _job_mark_current_shot(
                    jid,
                    sn,
                    ep_num=ep_num,
                    shot=shot_row,
                    index=i + 1,
                    total=n_vid,
                    progress=base,
                    phase="视频生成中",
                )

            def _tick(msg: str, _base: float = base, _label: str = label) -> None:
                if not _job_still_running(jid):
                    return
                _job_update(
                    jid,
                    "running",
                    _base + seg * 0.4,
                    f"{_label} · {msg}",
                    patch_params={"current_shot_num": int(sn)},
                )

            if only_shots:
                _job_update(jid, "running", base, f"{label} · 准备提交…")
            else:
                _job_update(
                    jid,
                    "running",
                    base,
                    f"{label}视频生成中 · 准备提交…",
                    patch_params={"current_shot_num": int(sn)},
                )
            extra_paths: List[Path] = []
            for rel in job_params.get("ref_image_paths") or shot_row.get("ref_images") or []:
                if isinstance(rel, dict):
                    rel = rel.get("path") or ""
                rel = str(rel or "").strip().lstrip("/")
                if not rel or ".." in rel.split("/"):
                    continue
                ap = db.project_dir(project_id) / rel
                if ap.is_file():
                    extra_paths.append(ap)
            mm.generate_shot_i2v(
                still_png=still,
                prompt=block,
                duration_sec=dur,
                dest_mp4=dest,
                on_progress=_tick,
                shot=shot_row,
                project_id=project_id,
                with_speech=with_audio,
                extra_ref_images=extra_paths,
            )
            if not _job_still_running(jid):
                # 本段可能已落盘，先写回再退出
                _reconcile_shot_videos_from_disk(
                    project_id, episode_id, ep_num, shots
                )
                return
            if not with_audio:
                _job_update(
                    jid, "running", base + seg * 0.85, f"{label} · 去除音轨…"
                )
                _strip_mp4_audio_inplace(dest)
            # 一段完成后立刻写库，工作台轮询即可看到本段视频
            _reconcile_shot_videos_from_disk(project_id, episode_id, ep_num, shots)
            _job_update(
                jid,
                "running",
                base + seg * 0.95,
                f"{label}已完成 · 进入下一段…"
                if i + 1 < n_vid
                else f"{label}已完成",
                patch_params={"current_shot_num": int(sn), "last_done_shot": int(sn)},
            )

    _reconcile_shot_videos_from_disk(project_id, episode_id, ep_num, shots)
    if not _job_still_running(jid):
        return

    if not auto_concat:
        done_msg = (
            f"整集分段视频已依次完成（官方 H3）：本轮新出 {generate_nums or '无'} 段；"
            "未拼接，可点「合并成片」"
            if not only_shots
            else (
                f"单段视频已生成（官方 H3）：镜 {video_shot_nums}"
                + ("（MiniMax 一次生成音轨）" if with_audio else "（已去音轨）")
            )
        )
        _job_update(
            jid,
            "done",
            1.0,
            done_msg,
            patch_params={"current_shot_num": None},
        )
        return

    # 合并：按全集镜号顺序拼接已有 mp4（缺镜则报错）
    concat_nums = list(video_shot_nums)
    clips: List[Path] = []
    missing_v: List[int] = []
    for sn in concat_nums:
        p = shots_dir / f"shot_{sn:02d}.mp4"
        if not _shot_asset_ready(p):
            missing_v.append(sn)
            continue
        clips.append(p)
    if missing_v:
        raise FileNotFoundError(
            f"镜 {missing_v} 尚无分镜视频，无法合并。请先「批量补缺分镜视频」。"
        )
    if not clips:
        raise FileNotFoundError("没有任何分镜视频可合并")

    out_mp4 = ep_dir / f"EP{ep_num:02d}_final.mp4"
    _job_update(jid, "running", 0.88, f"官方 H3 · ffmpeg 合并 {len(clips)} 镜…")
    _ffmpeg_concat_shot_mp4s(clips, out_mp4)
    rel_video = str(out_mp4.relative_to(db.project_dir(project_id))).replace("\\", "/")

    try:
        pl = json.loads(ep.get("pipeline_json") or "{}")
    except json.JSONDecodeError:
        pl = _default_episode_pipeline()
    pl["video"] = "done"
    if shots:
        pl["storyboard_images"] = pl.get("storyboard_images") or "done"
    update_episode(episode_id, video_path=rel_video, pipeline_json=pl)
    _job_update(
        jid,
        "done",
        1.0,
        f"{'合并成片' if mux_only else '成片'}完成（官方 H3）→ {out_mp4.name}",
    )


def _job_episode_video(
    jid: str,
    project_id: str,
    episode_id: str,
    job_params: Optional[Dict[str, Any]] = None,
) -> None:
    if not _job_still_running(jid):
        return
    from .studio_settings import video_backend, use_minimax_official

    # 官方 MiniMax API 已彻底关闭：一律走 Comfy / PPIO
    if video_backend() == "minimax" or use_minimax_official():
        raise RuntimeError(
            "官方 MiniMax API 已彻底关闭。请使用云 GPU ComfyUI（视频后端选 comfy）。"
        )
    job_params = job_params or {}
    # H3 原生带音轨；禁止静音片与 Web 侧补音
    job_params["preview_with_audio"] = True
    job_params["no_web_remux"] = True
    only_shots_early: Optional[List[int]] = None
    _raw_sn = job_params.get("shot_nums")
    if _raw_sn:
        only_shots_early = [int(x) for x in _raw_sn]
    # 镜长不足以说完末句时自动拉长，避免句尾被截断
    dur_fix = ensure_shot_durations_cover_dialogue(
        episode_id, shot_nums=only_shots_early
    )
    if dur_fix:
        _job_update(
            jid,
            "running",
            0.08,
            "已按对白拉长镜长："
            + "；".join(
                f"镜{x['shot_num']} {x['from']:.0f}→{x['to']:.0f}s" for x in dur_fix[:6]
            ),
        )
    ep = get_episode(episode_id)
    proj = get_project(project_id)
    shots = ep.get("shots") or []
    if only_shots_early:
        prompt = _build_drama10_prompt(
            project_id,
            episode_id,
            shot_nums=only_shots_early,
            for_video=True,
        )
    else:
        prompt = _build_drama10_prompt(project_id, episode_id, for_video=True)
    if not prompt.strip():
        raise ValueError("无 Drama10 脚本，请先生成 Prompt 或填写镜头")
    save_drama10_export_file(project_id, int(ep["ep_num"]), prompt)

    payload = {
        "shots": [
            {
                "id": s["shot_num"],
                "duration_sec": s["duration_sec"],
            }
            for s in shots
        ]
    }
    prefs = get_generation_prefs(project_id)
    src_w, src_h = resolve_video_source_canvas(proj, prefs)
    params = drama10_params(payload, src_w, src_h, None)
    ep_tag = f"ep{ep['ep_num']:02d}"
    vid_prefix = f"bingjing/{project_id[:8]}/{ep_tag}/final"
    sb_prefix = f"bingjing/{project_id[:8]}/{ep_tag}/storyboard_run"

    collage_rel: Optional[str] = None
    ep_dir = db.project_dir(project_id) / "episodes" / ep_tag
    mux_only = bool(job_params.get("mux_only"))
    shot_stills_subdir = _sync_episode_shot_stills_to_comfy(
        project_id, ep_tag, len(shots)
    )
    auto_sb = False
    only_shots: Optional[List[int]] = None
    _shot_nums_raw = job_params.get("shot_nums")
    if _shot_nums_raw:
        only_shots = [int(x) for x in _shot_nums_raw]
    if only_shots:
        _assert_shot_stills_for_video(
            project_id, episode_id, shot_nums=only_shots, mux_only=False
        )
    elif not mux_only:
        _assert_shot_stills_for_video(project_id, episode_id, shot_nums=None, mux_only=False)
    if not shot_stills_subdir:
        raise RuntimeError(
            "Comfy 未同步到分镜 PNG。请先在 Web 完成「生成此镜分镜图」并确认 episodes/…/shots/shot_XX.png 存在。"
        )
    if only_shots:
        _job_update(
            jid,
            "running",
            0.12,
            f"单镜 {only_shots} · 使用已有 shot_XX.png 图生视频…",
        )
    else:
        _job_update(jid, "running", 0.12, "整集 · 使用分镜图 shot_XX.png 逐镜图生视频…")

    vid_w, vid_h = adapt_canvas_for_video(src_w, src_h)
    n_shots = len(shots)
    video_shot_nums = [
        int(s["shot_num"])
        for s in shots
        if not _is_black_shot((s.get("visual") or ""))
    ]
    if not video_shot_nums:
        video_shot_nums = [int(s["shot_num"]) for s in shots]
    if only_shots:
        params = {
            **params,
            "preview_with_audio": job_params.get("preview_with_audio", True),
        }
    skip_final_mux = False
    if only_shots:
        want = sorted(set(only_shots))
        filtered = [sn for sn in video_shot_nums if sn in want]
        video_shot_nums = filtered if filtered else want
        skip_final_mux = True
    if only_shots:
        _job_update(
            jid,
            "running",
            0.13,
            f"单镜模式 · 镜 {video_shot_nums} · 画布 {vid_w}×{vid_h}（不拼接整集）…",
        )
    else:
        _job_update(
            jid,
            "running",
            0.13,
            f"整集成片 · 画布 {vid_w}×{vid_h} · 共 {len(video_shot_nums)} 镜…",
        )

    rerun_raw = job_params.get("rerun_shot_nums") or job_params.get("rerun_shots")
    rerun_shot_nums: Optional[List[int]] = None
    if rerun_raw and not mux_only:
        rerun_shot_nums = sorted(set(int(x) for x in rerun_raw))
    resume_ckpt = not bool(job_params.get("fresh_video"))
    # 单镜 PNG 图生视频：与单镜预览一致，逐镜不用旧 pt；整集开始前清缓存
    i2v_resume = resume_ckpt
    if rerun_shot_nums:
        n_del = _delete_comfy_shots_from_latest_ckpt(rerun_shot_nums)
        video_shot_nums = rerun_shot_nums
        i2v_resume = True
        _job_update(
            jid,
            "running",
            0.11,
            f"重跑镜 {rerun_shot_nums}（已清缓存 {n_del} 项）· 其余镜头 resume…",
        )
    elif shot_stills_subdir and not mux_only:
        if only_shots:
            want = sorted(set(only_shots))
            n_del = _delete_comfy_shots_from_latest_ckpt(want)
            i2v_resume = True
            _job_update(
                jid,
                "running",
                0.11,
                f"单镜 {want} · 已清本镜缓存 {n_del} 项（保留其余镜头，非整集重跑）…",
            )
        else:
            i2v_resume = False
            if job_params.get("fresh_video"):
                n_purged = _purge_comfy_drama_ckpt_cache()
                _job_update(
                    jid,
                    "running",
                    0.11,
                    f"整集 · 已清空 Drama10 i2v 缓存（{n_purged} 项）· 逐镜生成…",
                )
    elif job_params.get("fresh_video"):
        i2v_resume = False
        _job_update(jid, "running", 0.14, "全新成片：按当前分镜重跑 i2v…")

    # 继续补全：默认跳过已有分段视频（与官方 API 路径一致）
    skip_existing_comfy = (
        not mux_only
        and not only_shots
        and not rerun_shot_nums
        and not bool(job_params.get("fresh_video"))
        and bool(job_params.get("skip_existing", True))
    )
    if skip_existing_comfy and video_shot_nums:
        ep_num_i = int(ep["ep_num"])
        missing = [
            sn
            for sn in video_shot_nums
            if not _shot_asset_ready(_shot_video_mp4_path(project_id, ep_num_i, sn))
        ]
        kept = [sn for sn in video_shot_nums if sn not in missing]
        if kept:
            _job_update(
                jid,
                "running",
                0.12,
                f"已有分段跳过：{kept}；待补 {len(missing)} 段",
            )
        video_shot_nums = missing
        if not video_shot_nums and not mux_only:
            _reconcile_shot_videos_from_disk(
                project_id, episode_id, ep_num_i, shots
            )
            _job_update(
                jid,
                "done",
                1.0,
                "全部待做分段已有视频，已跳过；可点「合并成片」",
            )
            return

    segmented_i2v = False
    shot_by_num_comfy = {int(s["shot_num"]): s for s in shots}
    if _episode_video_use_segments() and video_shot_nums and not mux_only:
        segmented_i2v = True
        seg = 0.62 / max(len(video_shot_nums), 1)
        n_vid = len(video_shot_nums)
        for i, sn in enumerate(video_shot_nums):
            if not _job_still_running(jid):
                return
            base = 0.15 + i * seg
            shot_row = shot_by_num_comfy.get(int(sn)) or {}
            if only_shots:
                shot_label = f"单段-第{int(ep['ep_num'])}集镜{sn}"
            else:
                shot_label = _job_mark_current_shot(
                    jid,
                    int(sn),
                    ep_num=int(ep["ep_num"]),
                    shot=shot_row,
                    index=i + 1,
                    total=n_vid,
                    progress=base,
                    phase="视频生成中",
                )
            _job_update(
                jid,
                "running",
                base,
                f"{shot_label}视频生成中 · 准备提交 Comfy…",
                patch_params={"current_shot_num": int(sn)},
            )
            hist_shot = _comfy_episode_video_pass(
                jid,
                prompt=prompt,
                vid_w=vid_w,
                vid_h=vid_h,
                params=params,
                ep_num=int(ep["ep_num"]),
                auto_sb=auto_sb,
                collage_rel=collage_rel,
                shot_stills_subdir=shot_stills_subdir,
                vid_prefix=vid_prefix,
                sb_prefix=sb_prefix,
                single_shot_index=sn,
                finalize_mux=False,
                progress_base=base,
                progress_span=seg,
                wait_timeout=2400.0,
                pass_label=shot_label,
                resume_ckpt=i2v_resume,
            )
            if not _job_still_running(jid):
                _reconcile_shot_videos_from_disk(
                    project_id, episode_id, int(ep["ep_num"]), shots
                )
                return
            # 云 GPU：ckpt 在远端磁盘，立刻经 /view 落盘到项目，避免后续导入失败
            got = _import_shot_video_from_history(
                hist_shot,
                project_id,
                int(ep["ep_num"]),
                int(sn),
                vid_prefix=vid_prefix,
            )
            if got is None:
                _import_shot_videos_from_comfy_ckpt(
                    project_id,
                    int(ep["ep_num"]),
                    len(shots),
                    shot_nums=[int(sn)],
                    prefer_audio=True,
                )
            _reconcile_shot_videos_from_disk(
                project_id, episode_id, int(ep["ep_num"]), shots
            )
            dest_chk = (
                db.project_dir(project_id)
                / "episodes"
                / f"ep{int(ep['ep_num']):02d}"
                / "shots"
                / f"shot_{int(sn):02d}.mp4"
            )
            if not dest_chk.is_file():
                raise FileNotFoundError(f"{shot_label} 视频未落盘")
            _ensure_shot_audio_usable(
                project_id,
                episode_id,
                int(sn),
                dest_chk,
                shot_label=shot_label,
            )
            if i + 1 < n_vid:
                _job_update(
                    jid,
                    "running",
                    base + seg * 0.98,
                    f"{shot_label}已完成（含原生音轨）· 进入下一段…",
                    patch_params={
                        "current_shot_num": int(sn),
                        "last_done_shot": int(sn),
                    },
                )
            if sn != video_shot_nums[-1] and _episode_video_restart_comfy_between_shots():
                _restart_comfy_user_service()
    if (
        segmented_i2v
        and not skip_final_mux
        and not mux_only
        and bool(job_params.get("preview_with_audio", True))
    ):
        # 只要 H3 原生音轨：导入后校验
        _job_update(
            jid,
            "running",
            0.72,
            f"整集 · 导入 {len(video_shot_nums)} 镜并校验 H3 原生音轨…",
        )
        _import_shot_videos_from_comfy_ckpt(
            project_id,
            int(ep["ep_num"]),
            len(shots),
            shot_nums=video_shot_nums,
            prefer_audio=True,
        )
        for _sn in video_shot_nums:
            dest = (
                db.project_dir(project_id)
                / "episodes"
                / f"ep{int(ep['ep_num']):02d}"
                / "shots"
                / f"shot_{int(_sn):02d}.mp4"
            )
            if not dest.is_file():
                raise FileNotFoundError(f"镜 {_sn} 视频未落盘")
            _ensure_shot_audio_usable(
                project_id, episode_id, int(_sn), dest, shot_label=f"镜 {_sn}"
            )
        _reconcile_shot_videos_from_disk(
            project_id, episode_id, int(ep["ep_num"]), shots
        )
    if skip_final_mux and not mux_only and not segmented_i2v and video_shot_nums:
        sn = video_shot_nums[0]
        hist_one = _comfy_episode_video_pass(
            jid,
            prompt=prompt,
            vid_w=vid_w,
            vid_h=vid_h,
            params=params,
            ep_num=int(ep["ep_num"]),
            auto_sb=auto_sb,
            collage_rel=collage_rel,
            shot_stills_subdir=shot_stills_subdir,
            vid_prefix=vid_prefix,
            sb_prefix=sb_prefix,
            single_shot_index=sn,
            finalize_mux=False,
            progress_base=0.22,
            progress_span=0.68,
            wait_timeout=2400.0,
            pass_label=f"单镜 {sn} · 图生视频（非整集）",
            resume_ckpt=i2v_resume,
        )
        _import_shot_video_from_history(
            hist_one,
            project_id,
            int(ep["ep_num"]),
            int(sn),
            vid_prefix=vid_prefix,
        )
    if skip_final_mux:
        _job_update(
            jid,
            "running",
            0.9,
            f"单段完成 · 导入镜 {video_shot_nums} 并校验 H3 原生音轨…",
        )
        _import_shot_videos_from_comfy_ckpt(
            project_id,
            int(ep["ep_num"]),
            len(shots),
            shot_nums=video_shot_nums,
            prefer_audio=True,
        )
        for _sn in video_shot_nums:
            dest = (
                db.project_dir(project_id)
                / "episodes"
                / f"ep{int(ep['ep_num']):02d}"
                / "shots"
                / f"shot_{int(_sn):02d}.mp4"
            )
            if not dest.is_file():
                raise FileNotFoundError(f"镜 {_sn} 视频未落盘")
            _ensure_shot_audio_usable(
                project_id, episode_id, int(_sn), dest, shot_label=f"镜 {_sn}"
            )
        _reconcile_shot_videos_from_disk(project_id, episode_id, int(ep["ep_num"]), shots)
        _job_update(
            jid,
            "done",
            1.0,
            f"单段视频已生成（可用音轨）：镜 {video_shot_nums}（未拼接整集）",
        )
        return

    need_segment_mux = _episode_video_use_segments() and (
        len(video_shot_nums) >= 2 or bool(rerun_shot_nums)
    )
    if need_segment_mux:
        if mux_only:
            _job_update(
                jid,
                "running",
                0.2,
                "单镜视频已齐 · 拼接成片（跳过 i2v）…",
            )
        mux_label = "拼接成片"
        hist = _comfy_episode_video_pass(
            jid,
            prompt=prompt,
            vid_w=vid_w,
            vid_h=vid_h,
            params=params,
            ep_num=int(ep["ep_num"]),
            auto_sb=auto_sb,
            collage_rel=collage_rel,
            shot_stills_subdir=shot_stills_subdir,
            vid_prefix=vid_prefix,
            sb_prefix=sb_prefix,
            single_shot_index=0,
            finalize_mux=True,
            resume_ckpt=True,
            progress_base=0.78 if not mux_only else 0.2,
            progress_span=0.2 if not mux_only else 0.75,
            wait_timeout=3600.0,
            pass_label=mux_label,
        )
    else:
        hist = _comfy_episode_video_pass(
            jid,
            prompt=prompt,
            vid_w=vid_w,
            vid_h=vid_h,
            params=params,
            ep_num=int(ep["ep_num"]),
            auto_sb=auto_sb,
            collage_rel=collage_rel,
            shot_stills_subdir=shot_stills_subdir,
            vid_prefix=vid_prefix,
            sb_prefix=sb_prefix,
            progress_base=0.22,
            progress_span=0.68,
            wait_timeout=7200.0,
            pass_label="整集成片",
            resume_ckpt=i2v_resume if not mux_only else True,
        )

    _job_update(jid, "running", 0.94, "Comfy 已完成 · 正在定位成片文件…")
    imgs, vids = resolve_output_files(hist or {}, client=ComfyClient())
    video_src = vids[0] if vids else find_newest_under(vid_prefix, ".mp4")
    if video_src is None or not video_src.is_file():
        # 云 GPU 上 finalize 偶发未进 history：用已落盘单镜本地拼接兜底
        clips = []
        for sn in video_shot_nums:
            p = (
                db.project_dir(project_id)
                / "episodes"
                / f"ep{int(ep['ep_num']):02d}"
                / "shots"
                / f"shot_{int(sn):02d}.mp4"
            )
            if p.is_file() and p.stat().st_size > 512:
                clips.append(p)
        if len(clips) >= 1:
            out_mp4 = ep_dir / f"EP{ep['ep_num']:02d}_final.mp4"
            _job_update(jid, "running", 0.95, "成片未从 Comfy 取回 · 本地拼接单镜…")
            _ffmpeg_concat_shot_mp4s(clips, out_mp4)
            video_src = out_mp4
        else:
            raise FileNotFoundError("未找到 ComfyUI 成片 MP4")

    out_mp4 = ep_dir / f"EP{ep['ep_num']:02d}_final.mp4"
    _job_update(jid, "running", 0.97, f"正在写入 {out_mp4.name}…")
    copy_into_project(video_src, out_mp4)
    rel_video = str(out_mp4.relative_to(db.project_dir(project_id))).replace("\\", "/")

    _import_shot_videos_from_comfy_ckpt(project_id, int(ep["ep_num"]), len(shots))
    _reconcile_shot_videos_from_disk(project_id, episode_id, int(ep["ep_num"]), shots)

    if imgs:
        copy_into_project(imgs[0], ep_dir / "storyboard_from_video.png")

    try:
        pl = json.loads(ep.get("pipeline_json") or "{}")
    except json.JSONDecodeError:
        pl = _default_episode_pipeline()
    pl["video"] = "done"
    if not auto_sb and shots:
        pl["storyboard_images"] = pl.get("storyboard_images") or "done"
    update_episode(episode_id, video_path=rel_video, pipeline_json=pl)
    _job_update(jid, "done", 1.0, f"成片完成 → {out_mp4.name}")


def seed_bingjing_if_empty() -> Optional[str]:
    if list_projects():
        return None
    pid = create_project(
        name="冰镜",
        description="改编自《幻城》意象 · 20集短剧",
        episode_count=20,
        style_prefix="国风仙侠电影感，冰雪蓝调，横屏16比9，超清，无文字无水印，单镜头全屏电影画面",
        project_id=CANONICAL_BINGJING_PROJECT_ID,
    )["id"]
    from .voice_cast import DEFAULT_VOICE_CAST

    prefs = default_prefs()
    prefs["voice_cast"] = list(DEFAULT_VOICE_CAST)
    update_project_docs(
        pid,
        outline_md="刃雪城、卡索与樱空释、冰镜预言、潮雾之患。（详见 project 文档）",
        generation_prefs_json=prefs,
    )
    upsert_character(
        pid,
        {
            "name": "卡索",
            "role_note": "刃雪城王",
            "visual_prompt": "青年帝王，黑长发束冠，白狐裘，冷峻",
        },
    )
    upsert_character(
        pid,
        {"name": "樱空释", "role_note": "王弟", "visual_prompt": "银灰长发，白袍暗纹"},
    )
    upsert_scene(
        pid,
        {"code": "SC-01", "name": "刃雪城全景", "visual_prompt": "航拍冰雪要塞"},
    )
    upsert_scene(
        pid,
        {"code": "SC-03", "name": "冰镜禁室", "visual_prompt": "巨冰镜发光禁室"},
    )
    eps = list_episodes(pid)
    ep1 = next(e for e in eps if e["ep_num"] == 1)
    try:
        legacy = json.loads(
            (db.WEB_ROOT / "data" / "ep01.json").read_text(encoding="utf-8")
        )
        update_episode(
            ep1["id"],
            title=legacy.get("title", "王座上的雪"),
            script_md="（见服务器 project/04 分集剧本 EP01）",
        )
        shots = []
        for s in legacy.get("shots", []):
            shots.append(
                {
                    "shot_num": s["id"],
                    "duration_sec": s.get("duration_sec", 5),
                    "visual": s.get("visual", ""),
                    "motion": s.get("motion", ""),
                    "voices": s.get("voices", []),
                }
            )
        replace_shots(ep1["id"], shots)
    except OSError:
        pass
    return pid
