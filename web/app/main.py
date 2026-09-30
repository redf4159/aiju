# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import os
import secrets
import uuid
from pathlib import Path
from typing import Any, List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, services
from .comfy_service import collect_comfy_status, control_comfy
from .system_metrics import collect_system_metrics
from .comfy_client import fix_portrait_orientation
from .drama10_export import drama10_params, episode_to_drama10, load_episode as load_legacy_ep
from .pipeline_stages import EPISODE_STAGES, PROJECT_STAGES

WEB_USER = os.environ.get("WEB_USER", "sansuo")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "sansuo2026")

APP_DIR = Path(__file__).resolve().parent
STATIC = APP_DIR / "static"
OUTPUT = APP_DIR.parent / "output"
OUTPUT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="短剧制作台", version="0.2.0")


def _check_basic_auth(request: Request) -> bool:
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    user, password = decoded.split(":", 1)
    return secrets.compare_digest(user, WEB_USER) and secrets.compare_digest(
        password, WEB_PASSWORD
    )


@app.middleware("http")
async def require_login(request: Request, call_next):
    if _check_basic_auth(request):
        return await call_next(request)
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="bingjing"'},
        content="需要登录",
        media_type="text/plain; charset=utf-8",
    )


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    services.recover_jobs_on_startup()
    services.seed_bingjing_if_empty()
    services.recover_production_data_on_startup()


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ---------- Pages ----------

@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/comfy", response_class=HTMLResponse)
def comfy_admin_page():
    return FileResponse(STATIC / "comfy.html")


# ---------- Config ----------

@app.get("/api/system/metrics")
def api_system_metrics():
    """制作台主机实时负荷（CPU/内存/磁盘/GPU）。"""
    try:
        return collect_system_metrics()
    except Exception as e:
        raise HTTPException(500, f"读取系统指标失败: {e}")


@app.get("/api/comfy/status")
def api_comfy_status():
    try:
        return collect_comfy_status()
    except Exception as e:
        raise HTTPException(500, f"读取 ComfyUI 状态失败: {e}")


class ComfyControlBody(BaseModel):
    action: str = Field(..., pattern="^(start|stop|restart)$")
    force: bool = False


@app.post("/api/comfy/control")
def api_comfy_control(body: ComfyControlBody):
    try:
        return control_comfy(body.action, force=body.force)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, f"ComfyUI 操作失败: {e}")


@app.get("/api/config")
def api_config():
    return {
        "comfyui_url": os.environ.get("COMFYUI_URL", "http://127.0.0.1:8000"),
        "public_comfyui_url": os.environ.get(
            "PUBLIC_COMFYUI_URL", "http://175.155.64.171:61950"
        ),
        "web_base": os.environ.get(
            "WEB_PUBLIC_URL", "http://175.155.64.171:61951"
        ),
        "stages": {"project": PROJECT_STAGES, "episode": EPISODE_STAGES},
        "storyboard_backends": services.list_storyboard_backend_options(),
        "storyboard_image_api_ready": services.storyboard_image_api_ready(),
        "ohmygpt": services.get_ohmygpt_settings(),
        "script_llm_models": services.list_script_llm_model_options(),
    }


class OhmyGPTSettingsUpdate(BaseModel):
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    clear_key: bool = False


@app.get("/api/settings/ohmygpt")
def api_ohmygpt_settings_get():
    return services.get_ohmygpt_settings()


@app.put("/api/settings/ohmygpt")
def api_ohmygpt_settings_put(body: OhmyGPTSettingsUpdate):
    return services.save_ohmygpt_settings(
        api_base=body.api_base,
        api_key=body.api_key,
        clear_key=body.clear_key,
    )


class SeriesScriptsGenerateBody(BaseModel):
    from_ep: int = Field(default=1, ge=1, le=99)
    to_ep: int = Field(default=20, ge=1, le=99)
    model: Optional[str] = None
    regenerate_outline: bool = True
    sync_to_db: bool = True
    confirm: bool = False


@app.post("/api/projects/{project_id}/scripts/generate-ohmygpt")
def api_generate_series_scripts(project_id: str, body: SeriesScriptsGenerateBody):
    if not body.confirm:
        raise HTTPException(
            400,
            "会覆盖 web/data/epXX.json 并可选同步镜头表；请传 confirm=true。不会自动生成分镜/视频。",
        )
    try:
        return services.generate_series_scripts_via_ohmygpt(
            project_id,
            from_ep=body.from_ep,
            to_ep=body.to_ep,
            model=body.model,
            regenerate_outline=body.regenerate_outline,
            sync_to_db=body.sync_to_db,
        )
    except KeyError:
        raise HTTPException(404, "项目不存在")
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- Projects ----------

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    episode_count: int = Field(default=20, ge=1, le=99)
    width: int = 640
    height: int = 360
    style_prefix: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    episode_count: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    style_prefix: Optional[str] = None
    outline_md: Optional[str] = None
    series_scripts_note: Optional[str] = None
    generation_prefs: Optional[dict] = None


@app.get("/api/projects")
def api_projects_list():
    return {"projects": services.list_projects()}


@app.post("/api/projects")
def api_projects_create(body: ProjectCreate):
    return services.create_project(**body.model_dump())


@app.get("/api/projects/{project_id}")
def api_project_get(project_id: str):
    try:
        return services.get_project(project_id)
    except KeyError:
        raise HTTPException(404, "项目不存在")


@app.patch("/api/projects/{project_id}")
def api_project_patch(project_id: str, body: ProjectUpdate):
    try:
        data = body.model_dump(exclude_unset=True)
        doc_fields = {}
        for k in ("outline_md", "series_scripts_note"):
            if k in data:
                doc_fields[k] = data.pop(k)
        if "generation_prefs" in data:
            doc_fields["generation_prefs_json"] = data.pop("generation_prefs")
        proj = services.update_project(project_id, **data) if data else services.get_project(project_id)
        if doc_fields:
            gp_sync = None
            if "generation_prefs_json" in doc_fields and isinstance(
                doc_fields["generation_prefs_json"], dict
            ):
                gp_sync = doc_fields["generation_prefs_json"]
            proj = services.update_project_docs(project_id, **doc_fields)
            if gp_sync and gp_sync.get("voice_cast"):
                from .voice_cast import sync_voice_cast_to_characters

                sync_voice_cast_to_characters(
                    project_id, gp_sync.get("voice_cast") or [], episode_id=""
                )
        return proj
    except KeyError:
        raise HTTPException(404, "项目不存在")


@app.get("/api/projects/{project_id}/pipeline")
def api_project_pipeline(project_id: str):
    try:
        return services.compute_project_status(project_id)
    except KeyError:
        raise HTTPException(404, "项目不存在")


# ---------- Characters / Scenes ----------

class CharacterIn(BaseModel):
    id: Optional[str] = None
    episode_id: Optional[str] = None
    name: str
    role_note: str = ""
    visual_prompt: str = ""
    ref_image: str = ""
    voice_desc: str = ""
    tts_voice: str = ""
    voice_samples: Optional[List[str]] = None
    ref_images: Optional[List[dict]] = None
    sort_order: int = 0


class SceneIn(BaseModel):
    id: Optional[str] = None
    episode_id: Optional[str] = None
    code: str = ""
    name: str
    visual_prompt: str = ""
    ref_image: str = ""
    sort_order: int = 0


@app.get("/api/projects/{project_id}/characters")
def api_characters(project_id: str, episode_id: Optional[str] = None):
    return {"items": services.list_characters(project_id, episode_id)}


@app.post("/api/projects/{project_id}/characters")
def api_character_save(project_id: str, body: CharacterIn):
    return services.upsert_character(project_id, body.model_dump())


@app.post("/api/projects/{project_id}/characters/{character_id}/voices")
async def api_character_upload_voice(
    project_id: str, character_id: str, file: UploadFile = File(...)
):
    try:
        services.get_character(project_id, character_id)
    except KeyError:
        raise HTTPException(404, "人物不存在")
    ext = Path(file.filename or "voice.wav").suffix.lower()
    if ext not in (".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac"):
        ext = ".wav"
    dest_dir = db.project_dir(project_id) / "characters" / character_id / "voices"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"sample_{uuid.uuid4().hex[:10]}{ext}"
    dest.write_bytes(await file.read())
    rel = str(dest.relative_to(db.project_dir(project_id))).replace("\\", "/")
    try:
        return services.append_character_voice_sample(project_id, character_id, rel)
    except KeyError:
        raise HTTPException(404, "人物不存在")


class CharacterGenerateImageBody(BaseModel):
    angle_label: str = "正面"
    extra: str = ""


class CharacterGenerateVoiceBody(BaseModel):
    demo_text: Optional[str] = None
    voice: Optional[str] = None


@app.post("/api/projects/{project_id}/characters/{character_id}/generate-image")
def api_character_generate_image(
    project_id: str, character_id: str, body: CharacterGenerateImageBody
):
    try:
        return services.generate_character_image_via_ohmygpt(
            project_id,
            character_id,
            angle_label=body.angle_label,
            extra=body.extra,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "人物不存在")


@app.post("/api/projects/{project_id}/characters/{character_id}/generate-voice")
def api_character_generate_voice(
    project_id: str, character_id: str, body: CharacterGenerateVoiceBody
):
    try:
        return services.generate_character_voice_via_ohmygpt(
            project_id,
            character_id,
            demo_text=body.demo_text,
            voice=body.voice,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "人物不存在")


@app.post("/api/projects/{project_id}/characters/{character_id}/images")
async def api_character_upload_image(
    project_id: str,
    character_id: str,
    file: UploadFile = File(...),
    label: str = "",
):
    try:
        services.get_character(project_id, character_id)
    except KeyError:
        raise HTTPException(404, "人物不存在")
    ext = Path(file.filename or "img.png").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    dest_dir = db.project_dir(project_id) / "characters" / character_id / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"ref_{uuid.uuid4().hex[:10]}{ext}"
    dest.write_bytes(await file.read())
    rel = str(dest.relative_to(db.project_dir(project_id))).replace("\\", "/")
    try:
        return services.append_character_ref_image(
            project_id, character_id, rel, label=(label or "").strip() or "形象"
        )
    except KeyError:
        raise HTTPException(404, "人物不存在")


@app.get("/api/projects/{project_id}/scenes")
def api_scenes(project_id: str, episode_id: Optional[str] = None):
    return {"items": services.list_scenes(project_id, episode_id)}


@app.post("/api/projects/{project_id}/scenes")
def api_scene_save(project_id: str, body: SceneIn):
    return services.upsert_scene(project_id, body.model_dump())


# ---------- Episodes ----------

class EpisodeUpdate(BaseModel):
    title: Optional[str] = None
    script_md: Optional[str] = None
    storyboard_md: Optional[str] = None
    video_path: Optional[str] = None


class ShotsReplace(BaseModel):
    shots: List[dict]


@app.get("/api/projects/{project_id}/episodes")
def api_episodes_list(project_id: str):
    return {"episodes": services.list_episodes(project_id)}


@app.get("/api/episodes/{episode_id}")
def api_episode_get(episode_id: str):
    try:
        ep = services.get_episode(episode_id)
        st = services.compute_episode_status(episode_id)
        ep["pipeline"] = st["pipeline"]
        ep["production"] = st["detail"]
        return ep
    except KeyError:
        raise HTTPException(404, "集不存在")


@app.patch("/api/episodes/{episode_id}")
def api_episode_patch(episode_id: str, body: EpisodeUpdate):
    try:
        return services.update_episode(episode_id, **body.model_dump(exclude_unset=True))
    except KeyError:
        raise HTTPException(404, "集不存在")


@app.put("/api/episodes/{episode_id}/shots")
def api_episode_shots(episode_id: str, body: ShotsReplace):
    try:
        return services.replace_shots(episode_id, body.shots)
    except KeyError:
        raise HTTPException(404, "集不存在")


# ---------- Jobs ----------

class JobStart(BaseModel):
    stage_id: str
    episode_id: Optional[str] = None
    params: Optional[dict] = None


@app.get("/api/projects/{project_id}/jobs")
def api_jobs_list(project_id: str):
    return {"jobs": services.list_jobs(project_id)}


@app.post("/api/projects/{project_id}/jobs")
def api_job_start(project_id: str, body: JobStart):
    try:
        return services.start_job(
            project_id, body.stage_id, body.episode_id, body.params
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/jobs/{job_id}")
def api_job_get(job_id: str):
    try:
        return services.get_job(job_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")


# ---------- Files ----------

@app.get("/api/projects/{project_id}/files/{file_path:path}")
def api_project_file(project_id: str, file_path: str):
    base = db.project_dir(project_id).resolve()
    target = (base / file_path).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(target)


@app.post("/api/episodes/{episode_id}/shots/{shot_num}/image")
async def api_upload_shot_image(
    episode_id: str, shot_num: int, file: UploadFile = File(...)
):
    ep = services.get_episode(episode_id)
    proj_id = ep["project_id"]
    dest_dir = db.project_dir(proj_id) / "episodes" / f"ep{ep['ep_num']:02d}" / "shots"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "img.png").suffix or ".png"
    dest = dest_dir / f"shot_{shot_num:02d}{ext}"
    content = await file.read()
    dest.write_bytes(content)
    proj = services.get_project(proj_id)
    fix_portrait_orientation(dest, int(proj["width"]), int(proj["height"]))
    rel = str(dest.relative_to(db.project_dir(proj_id))).replace("\\", "/")
    with db.connect() as conn:
        conn.execute(
            "UPDATE shots SET image_path=? WHERE episode_id=? AND shot_num=?",
            (rel, episode_id, shot_num),
        )
    return {"image_path": rel, "url": f"/api/projects/{proj_id}/files/{rel}"}


@app.post("/api/episodes/{episode_id}/fix-orientation")
def api_fix_orientation(episode_id: str, shot_num: Optional[int] = None):
    try:
        ep = services.get_episode(episode_id)
        return services.fix_episode_shot_orientation(ep["project_id"], episode_id, shot_num)
    except KeyError:
        raise HTTPException(404, "集不存在")


@app.get("/api/episodes/{episode_id}/shots/{shot_num}/regen-preview")
def api_shot_regen_preview(episode_id: str, shot_num: int):
    try:
        ep = services.get_episode(episode_id)
        return services.shot_storyboard_regen_preview(
            ep["project_id"], episode_id, shot_num
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/episodes/{episode_id}/reprocess-storyboard-tiles")
def api_reprocess_storyboard_tiles(episode_id: str):
    """对已有单镜分镜图重新套用 STORYBOARD_TILE_ROTATE（整集方向一次性修复）。"""
    try:
        ep = services.get_episode(episode_id)
        return services.reprocess_episode_storyboard_tiles(ep["project_id"], episode_id)
    except KeyError:
        raise HTTPException(404, "集不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


# ---------- Legacy export (兼容) ----------

class ExportRequest(BaseModel):
    episode: int = Field(ge=1, le=99)
    width: int = 640
    height: int = 360
    shot_duration: Optional[float] = None


@app.post("/api/export/drama10")
def api_export_legacy(req: ExportRequest):
    try:
        ep = load_legacy_ep(req.episode)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    prompt = episode_to_drama10(ep)
    params = drama10_params(ep, req.width, req.height, req.shot_duration)
    out_path = OUTPUT / f"EP{req.episode:02d}_drama10_prompt.txt"
    out_path.write_text(prompt, encoding="utf-8")
    return {"prompt": prompt, "params": params, "saved_to": str(out_path)}
