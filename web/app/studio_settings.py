# -*- coding: utf-8 -*-
"""制作台全局设置（OhMyGPT 等），存 SQLite，不写进代码仓库。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import db

KEY_OHMYGPT_API_KEY = "ohmygpt_api_key"
KEY_OHMYGPT_API_BASE = "ohmygpt_api_base"
KEY_VIDEO_BACKEND = "video_backend"
KEY_COMFY_VIDEO_MODEL = "comfy_video_model"
KEY_PPIO_API_KEY = "ppio_api_key"
KEY_PPIO_ENDPOINT_KEY = "ppio_endpoint_key"
KEY_PPIO_ASYNC_BASE = "ppio_async_base"
KEY_MINIMAX_API_KEY = "minimax_api_key"
KEY_MINIMAX_API_BASE = "minimax_api_base"
KEY_MINIMAX_MODEL = "minimax_model"
KEY_MINIMAX_RESOLUTION = "minimax_resolution"

DEFAULT_OHMYGPT_API_BASE = "https://api.ohmygpt.com/v1"
# 官方 MiniMax API 已彻底关闭：默认且仅允许本机/隧道 Comfy（云 GPU H3）
DEFAULT_VIDEO_BACKEND = "comfy"  # comfy | ppio（minimax 官方 API 已禁用）
DEFAULT_PPIO_ASYNC_BASE = "https://async-public.serverless.ppinfra.com/v1"
DEFAULT_PPIO_ENDPOINT_KEY = "9bbe8e4bf9c2c122-duan"
DEFAULT_MINIMAX_API_BASE = "https://api.minimax.cn"
DEFAULT_MINIMAX_MODEL = "MiniMax-H3-Max"
DEFAULT_MINIMAX_RESOLUTION = "480P"
VALID_VIDEO_BACKENDS = ("comfy", "ppio")
VALID_COMFY_VIDEO_MODELS = ("minimax_h3", "ltx25")
DEFAULT_COMFY_VIDEO_MODEL = "minimax_h3"
VALID_MINIMAX_MODELS = ("MiniMax-H3", "MiniMax-H3-Max")
VALID_MINIMAX_RESOLUTIONS = ("480P", "768P", "2K")
# 硬开关：任何路径不得再走官方 MiniMax HTTP API
MINIMAX_OFFICIAL_API_DISABLED = True


def comfy_video_model() -> str:
    """Comfy 成片模型：minimax_h3 | ltx25。"""
    v = (_get(KEY_COMFY_VIDEO_MODEL) or DEFAULT_COMFY_VIDEO_MODEL).strip().lower()
    return v if v in VALID_COMFY_VIDEO_MODELS else DEFAULT_COMFY_VIDEO_MODEL


def use_ltx25_video() -> bool:
    return video_backend() == "comfy" and comfy_video_model() == "ltx25"


def _get(key: str) -> Optional[str]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM studio_settings WHERE key=?", (key,)
        ).fetchone()
    if not row:
        return None
    v = row["value"]
    return v if v else None


def _set(key: str, value: str) -> None:
    now = db._utcnow()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO studio_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now),
        )


def _delete(key: str) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM studio_settings WHERE key=?", (key,))


def mask_secret(value: str) -> str:
    v = (value or "").strip()
    if len(v) <= 8:
        return "已设置（已隐藏）"
    return f"{v[:4]}…{v[-4:]}"


def ohmygpt_api_base() -> str:
    return (_get(KEY_OHMYGPT_API_BASE) or DEFAULT_OHMYGPT_API_BASE).strip().rstrip("/")


def ohmygpt_api_key() -> str:
    return (_get(KEY_OHMYGPT_API_KEY) or "").strip()


def ohmygpt_configured() -> bool:
    import os

    if ohmygpt_api_key():
        return True
    return bool(
        (os.environ.get("STORYBOARD_IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    )


def get_ohmygpt_settings_public() -> Dict[str, Any]:
    key = ohmygpt_api_key()
    import os

    env_key = (
        os.environ.get("STORYBOARD_IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    ).strip()
    base = ohmygpt_api_base()
    if not _get(KEY_OHMYGPT_API_BASE) and os.environ.get("STORYBOARD_IMAGE_API_BASE"):
        base = os.environ.get("STORYBOARD_IMAGE_API_BASE", base).strip().rstrip("/")
    return {
        "provider": "ohmygpt",
        "api_base": base,
        "key_configured": bool(key or env_key),
        "key_source": "env" if (env_key and not key) else ("db" if key else "none"),
        "key_hint": mask_secret(key or env_key) if (key or env_key) else "",
    }


def update_ohmygpt_settings(
    *,
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    clear_key: bool = False,
) -> Dict[str, Any]:
    if api_base is not None:
        b = api_base.strip().rstrip("/")
        if b:
            _set(KEY_OHMYGPT_API_BASE, b)
    if clear_key:
        _delete(KEY_OHMYGPT_API_KEY)
    elif api_key is not None:
        k = api_key.strip()
        if k:
            _set(KEY_OHMYGPT_API_KEY, k)
    return get_ohmygpt_settings_public()


def video_backend() -> str:
    """视频任务后端：comfy（云 GPU / 隧道）| ppio。官方 minimax API 已禁用。"""
    import os

    v = (_get(KEY_VIDEO_BACKEND) or os.environ.get("VIDEO_BACKEND") or DEFAULT_VIDEO_BACKEND)
    v = (v or DEFAULT_VIDEO_BACKEND).strip().lower()
    if v == "minimax":
        return "comfy"
    return v if v in VALID_VIDEO_BACKENDS else DEFAULT_VIDEO_BACKEND


def minimax_api_key() -> str:
    """官方 API 已关闭：永不返回可用 Key。"""
    return ""


def minimax_api_base() -> str:
    return DEFAULT_MINIMAX_API_BASE


def minimax_model() -> str:
    return DEFAULT_MINIMAX_MODEL


def minimax_resolution() -> str:
    return DEFAULT_MINIMAX_RESOLUTION


def minimax_ready() -> bool:
    return False


def use_minimax_official() -> bool:
    """彻底禁用官方 MiniMax API。"""
    return False


def ppio_api_key() -> str:
    import os

    return (_get(KEY_PPIO_API_KEY) or os.environ.get("PPIO_API_KEY") or "").strip()


def ppio_endpoint_key() -> str:
    import os

    dbv = (_get(KEY_PPIO_ENDPOINT_KEY) or "").strip()
    if dbv:
        return dbv
    env_key = (os.environ.get("PPIO_ENDPOINT_KEY") or "").strip()
    if env_key:
        return env_key
    eid = (os.environ.get("PPIO_ENDPOINT_ID") or "").strip()
    app = (os.environ.get("PPIO_APP_NAME") or "").strip()
    if eid and app:
        return f"{eid}-{app}"
    return DEFAULT_PPIO_ENDPOINT_KEY


def ppio_async_base() -> str:
    import os

    return (
        _get(KEY_PPIO_ASYNC_BASE)
        or os.environ.get("PPIO_ASYNC_BASE")
        or DEFAULT_PPIO_ASYNC_BASE
    ).strip().rstrip("/")


def ppio_ready() -> bool:
    return bool(ppio_api_key() and ppio_endpoint_key())


def use_ppio_video() -> bool:
    return video_backend() == "ppio" and ppio_ready()


def active_video_backend() -> str:
    if use_ppio_video():
        return "ppio"
    return "comfy"


def get_video_backend_settings_public() -> Dict[str, Any]:
    ppio_key = ppio_api_key()
    backend = video_backend()
    active = active_video_backend()
    model = comfy_video_model()
    notes = {
        "ppio": "PPIO Async Serverless；Endpoint 内需有 H3/Drama10 镜像。",
        "comfy": "云 GPU / 隧道 ComfyUI 本地推理（可选 MiniMax H3 或 LTX-2.5）。",
    }
    warn = ""
    if backend == "ppio" and not ppio_ready():
        warn = "已选 PPIO 但密钥未齐，将回退 Comfy。"
    model_note = (
        "当前模型 LTX-2.5（INT8，偏音频稳定）。"
        if model == "ltx25"
        else "当前模型 MiniMax H3（偏人脸/口型）。"
    )
    return {
        "backend": backend,
        "comfy_video_model": model,
        "comfy_video_models": [
            {
                "id": "minimax_h3",
                "label": "MiniMax H3（人脸/口型优先）",
            },
            {
                "id": "ltx25",
                "label": "LTX-2.5 INT8（音频稳定优先）",
            },
        ],
        "backends": [
            {
                "id": "comfy",
                "label": "云 GPU ComfyUI",
                "ready": True,
            },
            {
                "id": "ppio",
                "label": "PPIO Serverless Endpoint",
                "ready": ppio_ready(),
            },
            {
                "id": "minimax",
                "label": "MiniMax 官方 API（已关闭）",
                "ready": False,
                "disabled": True,
            },
        ],
        "minimax": {
            "api_base": "",
            "model": "",
            "resolution": "",
            "key_configured": False,
            "key_hint": "",
            "ready": False,
            "disabled": True,
            "note": "官方 MiniMax API 已彻底关闭，仅使用云 GPU Comfy。",
        },
        "ppio": {
            "async_base": ppio_async_base(),
            "endpoint_key": ppio_endpoint_key(),
            "key_configured": bool(ppio_key),
            "key_hint": mask_secret(ppio_key) if ppio_key else "",
            "ready": ppio_ready(),
        },
        "active": active,
        "minimax_official_disabled": True,
        "note": notes.get(active, "")
        + " "
        + model_note
        + " 官方 MiniMax API 已关闭。"
        + ((" " + warn) if warn else ""),
    }


def update_video_backend_settings(
    *,
    backend: Optional[str] = None,
    comfy_video_model: Optional[str] = None,
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
    if backend is not None:
        b = backend.strip().lower()
        if b == "minimax":
            raise ValueError("官方 MiniMax API 已彻底关闭，请使用云 GPU Comfy（backend=comfy）")
        if b not in VALID_VIDEO_BACKENDS:
            raise ValueError("backend 仅支持 comfy / ppio（官方 minimax 已关闭）")
        _set(KEY_VIDEO_BACKEND, b)
    if comfy_video_model is not None:
        m = comfy_video_model.strip().lower()
        if m not in VALID_COMFY_VIDEO_MODELS:
            raise ValueError("comfy_video_model 仅支持 minimax_h3 / ltx25")
        _set(KEY_COMFY_VIDEO_MODEL, m)
    # 无论请求如何，清掉库内官方 Key，防止误用
    if MINIMAX_OFFICIAL_API_DISABLED:
        _delete(KEY_MINIMAX_API_KEY)
        if (_get(KEY_VIDEO_BACKEND) or "").strip().lower() == "minimax":
            _set(KEY_VIDEO_BACKEND, "comfy")
    if ppio_async_base is not None:
        base = ppio_async_base.strip().rstrip("/")
        if base:
            _set(KEY_PPIO_ASYNC_BASE, base)
    if ppio_endpoint_key is not None:
        ek = ppio_endpoint_key.strip()
        if ek:
            _set(KEY_PPIO_ENDPOINT_KEY, ek)
    if clear_ppio_key:
        _delete(KEY_PPIO_API_KEY)
    elif ppio_api_key is not None:
        k = ppio_api_key.strip()
        if k:
            _set(KEY_PPIO_API_KEY, k)
    if minimax_api_base is not None:
        mb = minimax_api_base.strip().rstrip("/")
        if mb:
            _set(KEY_MINIMAX_API_BASE, mb)
    if minimax_model is not None:
        mm = minimax_model.strip()
        if mm not in VALID_MINIMAX_MODELS:
            raise ValueError("minimax_model 仅支持 MiniMax-H3 或 MiniMax-H3-Max")
        _set(KEY_MINIMAX_MODEL, mm)
    if minimax_resolution is not None:
        mr = minimax_resolution.strip().upper()
        if mr not in VALID_MINIMAX_RESOLUTIONS:
            raise ValueError("minimax_resolution 仅支持 480P / 768P / 2K")
        model_now = (
            (minimax_model.strip() if minimax_model else "")
            or (_get(KEY_MINIMAX_MODEL) or DEFAULT_MINIMAX_MODEL)
        )
        if model_now == "MiniMax-H3" and mr == "480P":
            raise ValueError("MiniMax-H3 不支持 480P，请用 768P 或 2K")
        if model_now == "MiniMax-H3-Max" and mr == "2K":
            raise ValueError("MiniMax-H3-Max 不支持 2K，请用 480P 或 768P")
        _set(KEY_MINIMAX_RESOLUTION, mr)
    if clear_minimax_key or MINIMAX_OFFICIAL_API_DISABLED:
        _delete(KEY_MINIMAX_API_KEY)
    elif minimax_api_key is not None:
        # 官方 API 关闭期间拒绝写入新 Key
        if MINIMAX_OFFICIAL_API_DISABLED:
            raise ValueError("官方 MiniMax API 已关闭，无法保存 API Key")
        mk = minimax_api_key.strip()
        if mk:
            _set(KEY_MINIMAX_API_KEY, mk)
    return get_video_backend_settings_public()
