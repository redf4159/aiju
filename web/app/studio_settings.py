# -*- coding: utf-8 -*-
"""制作台全局设置（OhMyGPT 等），存 SQLite，不写进代码仓库。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import db

KEY_OHMYGPT_API_KEY = "ohmygpt_api_key"
KEY_OHMYGPT_API_BASE = "ohmygpt_api_base"

DEFAULT_OHMYGPT_API_BASE = "https://api.ohmygpt.com/v1"


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
