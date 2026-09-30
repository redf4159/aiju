# -*- coding: utf-8 -*-
"""分镜静帧：OpenAI 兼容 /v1/images/generations（模型广场、OpenRouter 等）。"""
from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import httpx
import time

from .studio_settings import DEFAULT_OHMYGPT_API_BASE, ohmygpt_api_base, ohmygpt_api_key
from .studio_settings import ohmygpt_configured as image_api_configured

BACKEND_IMAGE_API = "image_api"


def _resolve_api_credentials() -> Tuple[str, str]:
    key = ohmygpt_api_key() or (
        os.environ.get("STORYBOARD_IMAGE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    base = ohmygpt_api_base()
    if not base:
        base = (os.environ.get("STORYBOARD_IMAGE_API_BASE") or DEFAULT_OHMYGPT_API_BASE).strip()
    base = base.rstrip("/")
    return base, key


def image_api_model(prefs: Optional[dict] = None) -> str:
    if prefs and (prefs.get("storyboard_image_model") or "").strip():
        return str(prefs["storyboard_image_model"]).strip()
    return (
        os.environ.get("STORYBOARD_IMAGE_MODEL")
        or os.environ.get("STORYBOARD_IMAGE_API_MODEL")
        or "gpt-image-2.5-flare"
    ).strip()


def _snap_dim(n: int) -> int:
    n = int(n)
    return max(16, (n // 16) * 16)


def _portrait_api_size(width: int, height: int) -> str:
    custom = (os.environ.get("STORYBOARD_IMAGE_SIZE") or "").strip()
    if custom and "x" in custom.lower():
        a, _, b = custom.lower().partition("x")
        try:
            return f"{_snap_dim(int(a.strip()))}x{_snap_dim(int(b.strip()))}"
        except ValueError:
            pass
    if height > width:
        raw = os.environ.get("STORYBOARD_IMAGE_SIZE_PORTRAIT", "1024x1536")
        if "x" in raw.lower():
            a, _, b = raw.lower().partition("x")
            try:
                return f"{_snap_dim(int(a))}x{_snap_dim(int(b))}"
            except ValueError:
                return raw
        return raw
    default = os.environ.get("STORYBOARD_IMAGE_SIZE_LANDSCAPE", "1536x864")
    if "x" in default.lower():
        a, _, b = default.lower().partition("x")
        try:
            return f"{_snap_dim(int(a))}x{_snap_dim(int(b))}"
        except ValueError:
            pass
    return f"{_snap_dim(width)}x{_snap_dim(height)}"


def _merge_prompt(prompt: str, negative: str) -> str:
    p = (prompt or "").strip()
    n = (negative or "").strip()
    if not n:
        return p
    if "避免" in p or "negative" in p.lower():
        return p
    return f"{p}\n\nAvoid: {n}"


def generate_still_to_file(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    dest: Path,
    *,
    model: Optional[str] = None,
    timeout: float = 300.0,
) -> Path:
    """调用 OhMyGPT 等 OpenAI 兼容文生图，写入 dest（PNG）。"""
    base, key = _resolve_api_credentials()
    if not key:
        raise RuntimeError(
            "未配置 OhMyGPT：请在制作台「全局制作 → OhMyGPT 密钥」保存 API Key"
        )
    model = model or image_api_model()
    url = f"{base}/images/generations"
    if not url.startswith("http"):
        url = f"https://{url.lstrip('/')}"
    body = {
        "model": model,
        "prompt": _merge_prompt(prompt, negative)[:4000],
        "n": 1,
        "size": _portrait_api_size(width, height),
    }
    fmt = (os.environ.get("STORYBOARD_IMAGE_RESPONSE") or "b64_json").strip()
    if fmt:
        body["response_format"] = fmt

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    proxy = (os.environ.get("STORYBOARD_IMAGE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY") or "").strip()
    client_kw: dict = {"timeout": httpx.Timeout(timeout, connect=min(120.0, timeout))}
    if proxy:
        client_kw["proxy"] = proxy
    last_err: Optional[Exception] = None
    data = None
    for attempt in range(3):
        try:
            with httpx.Client(**client_kw) as client:
                resp = client.post(url, json=body, headers=headers)
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"出图 API HTTP {resp.status_code}: {resp.text[:500]}"
                    )
                data = resp.json()
            break
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            last_err = exc
            if attempt >= 2:
                raise
            time.sleep(2.0 * (attempt + 1))
    if data is None and last_err:
        raise last_err

    b64, img_url = _extract_image_payload(data)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if b64:
        raw = base64.b64decode(b64)
        dest.write_bytes(raw)
        return dest
    if img_url:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(img_url)
            r.raise_for_status()
            dest.write_bytes(r.content)
        return dest
    raise RuntimeError(f"出图 API 响应无图片数据: {str(data)[:300]}")


def _extract_image_payload(data: dict) -> Tuple[Optional[str], Optional[str]]:
    items = data.get("data") or []
    if not items:
        return None, None
    item = items[0]
    if isinstance(item, dict):
        if item.get("b64_json"):
            return item["b64_json"], None
        if item.get("url"):
            return None, item["url"]
    return None, None


def generate_still_via_api_temp(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    *,
    model: Optional[str] = None,
) -> Path:
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    path = Path(name)
    try:
        return generate_still_to_file(
            prompt, negative, width, height, path, model=model
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
