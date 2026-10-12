# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import quote, unquote, urlencode

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
# 媒体直链签名密钥（给 <video> 流式播放 / 拖拽进度用，避免 Basic Auth 丢头）
FILE_TOKEN_SECRET = (
    os.environ.get("BINGJING_FILE_TOKEN_SECRET") or WEB_PASSWORD or "bingjing"
).encode("utf-8")
MEDIA_TOKEN_TTL_SEC = int(os.environ.get("BINGJING_MEDIA_TOKEN_TTL", "7200"))

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


def _issue_media_token(project_id: str, file_path: str, ttl_sec: int = MEDIA_TOKEN_TTL_SEC) -> str:
    exp = int(time.time()) + max(60, int(ttl_sec))
    msg = f"{project_id}\n{file_path}\n{exp}".encode("utf-8")
    sig = hmac.new(FILE_TOKEN_SECRET, msg, hashlib.sha256).hexdigest()[:32]
    raw = f"{exp}.{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _verify_media_token(project_id: str, file_path: str, token: str) -> bool:
    if not token or not project_id or not file_path:
        return False
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode((token + pad).encode("ascii")).decode("utf-8")
        exp_s, sig = raw.split(".", 1)
        exp = int(exp_s)
    except Exception:
        return False
    if exp < int(time.time()):
        return False
    msg = f"{project_id}\n{file_path}\n{exp}".encode("utf-8")
    expect = hmac.new(FILE_TOKEN_SECRET, msg, hashlib.sha256).hexdigest()[:32]
    return secrets.compare_digest(sig, expect)


def _parse_project_file_path(url_path: str) -> Tuple[Optional[str], Optional[str]]:
    """从 /api/projects/{pid}/files/{rel} 解析 pid 与相对路径。"""
    prefix = "/api/projects/"
    marker = "/files/"
    if not url_path.startswith(prefix) or marker not in url_path:
        return None, None
    rest = url_path[len(prefix) :]
    pid, _, fpath = rest.partition(marker)
    if not pid or not fpath:
        return None, None
    return unquote(pid), unquote(fpath)


@app.middleware("http")
async def require_login(request: Request, call_next):
    if _check_basic_auth(request):
        return await call_next(request)
    # 允许带短期 media_token 的媒体直链（供 video 标签 Range 流式播放）
    token = request.query_params.get("media_token") or request.query_params.get("mt")
    if token and request.url.path.startswith("/api/projects/") and "/files/" in request.url.path:
        pid, fpath = _parse_project_file_path(request.url.path)
        if pid and fpath and _verify_media_token(pid, fpath, token):
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
        "video_backend": services.get_video_backend_settings(),
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


class VideoBackendSettingsUpdate(BaseModel):
    backend: Optional[str] = None
    ppio_api_key: Optional[str] = None
    ppio_endpoint_key: Optional[str] = None
    ppio_async_base: Optional[str] = None
    clear_ppio_key: bool = False
    minimax_api_key: Optional[str] = None
    minimax_api_base: Optional[str] = None
    minimax_model: Optional[str] = None
    minimax_resolution: Optional[str] = None
    clear_minimax_key: bool = False


@app.get("/api/settings/video-backend")
def api_video_backend_settings_get():
    return services.get_video_backend_settings()


@app.put("/api/settings/video-backend")
def api_video_backend_settings_put(body: VideoBackendSettingsUpdate):
    try:
        return services.save_video_backend_settings(
            backend=body.backend,
            ppio_api_key=body.ppio_api_key,
            ppio_endpoint_key=body.ppio_endpoint_key,
            ppio_async_base=body.ppio_async_base,
            clear_ppio_key=body.clear_ppio_key,
            minimax_api_key=body.minimax_api_key,
            minimax_api_base=body.minimax_api_base,
            minimax_model=body.minimax_model,
            minimax_resolution=body.minimax_resolution,
            clear_minimax_key=body.clear_minimax_key,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


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


@app.delete("/api/projects/{project_id}")
def api_project_delete(project_id: str):
    try:
        services.delete_project(project_id)
        return {"ok": True, "deleted": project_id}
    except KeyError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))


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
    episodes: Optional[List[int]] = None
    sort_order: int = 0


class SceneIn(BaseModel):
    id: Optional[str] = None
    episode_id: Optional[str] = None
    code: str = ""
    name: str
    visual_prompt: str = ""
    ref_image: str = ""
    ref_images: Optional[List[dict]] = None
    episodes: Optional[List[int]] = None
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


class PropIn(BaseModel):
    id: Optional[str] = None
    episode_id: Optional[str] = None
    name: str
    role_note: str = ""
    visual_prompt: str = ""
    ref_image: str = ""
    ref_images: Optional[List[dict]] = None
    episodes: Optional[List[int]] = None
    sort_order: int = 0


@app.get("/api/projects/{project_id}/props")
def api_props(project_id: str, episode_id: Optional[str] = None):
    return {"items": services.list_props(project_id, episode_id)}


@app.post("/api/projects/{project_id}/props")
def api_prop_save(project_id: str, body: PropIn):
    return services.upsert_prop(project_id, body.model_dump())


@app.post("/api/projects/{project_id}/props/{prop_id}/images")
async def api_prop_upload_image(
    project_id: str,
    prop_id: str,
    file: UploadFile = File(...),
    label: str = "",
):
    try:
        services.get_prop(project_id, prop_id)
    except KeyError:
        raise HTTPException(404, "道具不存在")
    ext = Path(file.filename or "img.png").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    dest_dir = db.project_dir(project_id) / "props" / prop_id / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"ref_{uuid.uuid4().hex[:10]}{ext}"
    dest.write_bytes(await file.read())
    rel = str(dest.relative_to(db.project_dir(project_id))).replace("\\", "/")
    try:
        return services.append_prop_ref_image(
            project_id, prop_id, rel, label=(label or "").strip() or "形象"
        )
    except KeyError:
        raise HTTPException(404, "道具不存在")


# ---------- 漫剧工作台流水线 ----------

class StudioImportBody(BaseModel):
    name: str = ""
    novel_text: str = Field(min_length=1)
    art_style: str = "半写实3D国漫"
    aspect_ratio: str = "16:9"
    mode: str = "默认版"
    description: str = ""


class StudioParseBody(BaseModel):
    use_llm: bool = True
    model: Optional[str] = None


@app.post("/api/studio/import")
def api_studio_import(body: StudioImportBody):
    from . import studio_pipeline as sp

    try:
        return sp.import_novel_project(
            name=body.name,
            novel_text=body.novel_text,
            art_style=body.art_style,
            aspect_ratio=body.aspect_ratio,
            mode=body.mode,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/studio/import-file")
async def api_studio_import_file(
    file: UploadFile = File(...),
    name: str = "",
    art_style: str = "半写实3D国漫",
    aspect_ratio: str = "16:9",
    mode: str = "默认版",
):
    from . import studio_pipeline as sp

    raw = await file.read()
    text = ""
    fname = (file.filename or "").lower()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="ignore")
    if fname.endswith(".docx"):
        raise HTTPException(400, "暂请另存为 TXT 后上传（DOCX 解析待接入）")
    if fname.endswith(".pdf"):
        raise HTTPException(400, "暂请另存为 TXT 后上传（PDF 解析待接入）")
    try:
        return sp.import_novel_project(
            name=name or Path(file.filename or "剧本").stem,
            novel_text=text,
            art_style=art_style,
            aspect_ratio=aspect_ratio,
            mode=mode,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/projects/{project_id}/studio/parse-script")
def api_studio_parse_script(project_id: str, body: StudioParseBody = StudioParseBody()):
    from . import studio_pipeline as sp

    try:
        return sp.parse_project_script(
            project_id, use_llm=body.use_llm, model=body.model
        )
    except KeyError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/api/projects/{project_id}/studio/parse-assets")
def api_studio_parse_assets(project_id: str, body: StudioParseBody = StudioParseBody()):
    from . import studio_pipeline as sp

    try:
        return sp.parse_project_assets(
            project_id, use_llm=body.use_llm, model=body.model
        )
    except KeyError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/api/projects/{project_id}/episodes/{episode_id}/studio/video-script")
def api_studio_video_script(
    project_id: str, episode_id: str, body: StudioParseBody = StudioParseBody()
):
    from . import studio_pipeline as sp

    try:
        return sp.generate_episode_video_script(
            project_id, episode_id, use_llm=body.use_llm, model=body.model
        )
    except KeyError:
        raise HTTPException(404, "分集不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


class BatchAssetImagesBody(BaseModel):
    kind: str = "characters"  # characters | scenes | props
    only_missing: bool = True
    limit: int = Field(default=12, ge=1, le=40)
    ids: Optional[List[str]] = None


class BatchVideoScriptsBody(BaseModel):
    use_llm: bool = True
    model: Optional[str] = None
    only_missing: bool = True
    limit: int = Field(default=10, ge=1, le=40)
    episode_ids: Optional[List[str]] = None


class CompleteContentBody(BaseModel):
    use_llm: bool = True
    model: Optional[str] = None
    include_assets: bool = True
    include_outline: bool = True
    include_video_scripts: bool = True
    only_missing_scripts: bool = True
    script_limit: int = Field(default=20, ge=1, le=40)
    asset_ids: Optional[List[str]] = None
    episode_ids: Optional[List[str]] = None


class EnrichDescriptionsBody(BaseModel):
    use_llm: bool = True
    model: Optional[str] = None
    asset_ids: Optional[List[str]] = None


@app.post("/api/projects/{project_id}/studio/enrich-descriptions")
def api_studio_enrich_descriptions(
    project_id: str, body: EnrichDescriptionsBody = EnrichDescriptionsBody()
):
    from . import studio_pipeline as sp

    try:
        return sp.enrich_existing_asset_descriptions(
            project_id,
            use_llm=body.use_llm,
            model=body.model,
            asset_ids=body.asset_ids,
        )
    except KeyError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/api/projects/{project_id}/studio/complete-content")
def api_studio_complete_content(project_id: str, body: CompleteContentBody):
    from . import studio_pipeline as sp

    try:
        return sp.complete_project_content(
            project_id,
            use_llm=body.use_llm,
            model=body.model,
            include_assets=body.include_assets,
            include_outline=body.include_outline,
            include_video_scripts=body.include_video_scripts,
            only_missing_scripts=body.only_missing_scripts,
            script_limit=body.script_limit,
            asset_ids=body.asset_ids,
            episode_ids=body.episode_ids,
        )
    except KeyError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/api/projects/{project_id}/studio/batch-generate-images")
def api_studio_batch_images(project_id: str, body: BatchAssetImagesBody):
    try:
        return services.batch_generate_asset_images(
            project_id,
            kind=body.kind,
            only_missing=body.only_missing,
            limit=body.limit,
            ids=body.ids,
        )
    except KeyError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/projects/{project_id}/studio/batch-video-scripts")
def api_studio_batch_video_scripts(project_id: str, body: BatchVideoScriptsBody):
    from . import studio_pipeline as sp

    try:
        services.get_project(project_id)
    except KeyError:
        raise HTTPException(404, "项目不存在")
    eps = services.list_episodes(project_id)
    id_set = {str(x).strip() for x in (body.episode_ids or []) if str(x).strip()}
    out = []
    errors = []
    n = 0
    limit = body.limit
    if id_set:
        limit = max(limit, len(id_set))
    for ep in eps:
        if id_set and ep["id"] not in id_set:
            continue
        if body.only_missing and (ep.get("storyboard_md") or "").strip():
            continue
        if n >= limit:
            break
        try:
            r = sp.generate_episode_video_script(
                project_id,
                ep["id"],
                use_llm=body.use_llm,
                model=body.model,
            )
            out.append(
                {
                    "episode_id": ep["id"],
                    "ep_num": ep.get("ep_num"),
                    "shot_count": r.get("shot_count"),
                }
            )
            n += 1
        except Exception as e:
            errors.append(
                {
                    "episode_id": ep["id"],
                    "ep_num": ep.get("ep_num"),
                    "error": str(e)[:200],
                }
            )
            n += 1
    return {"generated": len(out), "failed": len(errors), "items": out, "errors": errors}


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
        # 复用已取到的 ep，避免再跑一遍 get_episode（打开成片会明显变慢）
        st = services.compute_episode_status(episode_id, ep_row=ep, project_id=ep.get("project_id"))
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
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/jobs/{job_id}")
def api_job_get(job_id: str):
    try:
        return services.get_job(job_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")


@app.post("/api/jobs/{job_id}/stop")
def api_job_stop(job_id: str):
    """停止进行中的任务；已生成段落保留，可再提交「继续补全」。"""
    try:
        return services.cancel_job(job_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- Files ----------

@app.get("/api/projects/{project_id}/media-url")
def api_project_media_url(project_id: str, path: str, v: Optional[str] = None):
    """签发可给 <video> 直接播放的短期 URL（支持 Range / 拖拽进度，无需整文件下载）。"""
    rel = (path or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise HTTPException(400, "非法路径")
    base = db.project_dir(project_id).resolve()
    target = (base / rel).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(404, "文件不存在")
    token = _issue_media_token(project_id, rel)
    path_enc = "/".join(quote(seg, safe="") for seg in rel.split("/"))
    q = {"media_token": token}
    if v:
        q["v"] = v
    return {
        "url": f"/api/projects/{project_id}/files/{path_enc}?{urlencode(q)}",
        "path": rel,
        "expires_in": MEDIA_TOKEN_TTL_SEC,
    }


@app.get("/api/projects/{project_id}/files/{file_path:path}")
def api_project_file(project_id: str, file_path: str):
    base = db.project_dir(project_id).resolve()
    target = (base / file_path).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(404, "文件不存在")
    # FileResponse 支持 Range，浏览器可边下边播、拖拽进度条
    return FileResponse(
        target,
        filename=target.name,
        media_type=None,
    )

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
