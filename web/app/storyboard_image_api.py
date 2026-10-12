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


def _env_size(name: str) -> Optional[str]:
    raw = (os.environ.get(name) or "").strip()
    if not raw or "x" not in raw.lower():
        return None
    a, _, b = raw.lower().partition("x")
    try:
        return f"{_snap_dim(int(a.strip()))}x{_snap_dim(int(b.strip()))}"
    except ValueError:
        return None


def _scale_canvas_to_api_budget(width: int, height: int) -> Tuple[int, int]:
    """按项目宽高比缩放，直到满足上游常见像素/短边下限（避免 400 invalid size）。"""
    w = _snap_dim(max(64, int(width)))
    h = _snap_dim(max(64, int(height)))
    # 默认 0：上游优先用项目原尺寸；若 API 拒尺寸再走 fallback 同比例放大
    try:
        min_pixels = int((os.environ.get("STORYBOARD_IMAGE_MIN_PIXELS") or "0").strip())
    except ValueError:
        min_pixels = 0
    try:
        min_short = int((os.environ.get("STORYBOARD_IMAGE_MIN_SHORT") or "0").strip())
    except ValueError:
        min_short = 0
    scale = 1.0
    if min_pixels > 0 and w * h < min_pixels:
        scale = max(scale, (min_pixels / float(w * h)) ** 0.5)
    if min_short > 0 and min(w, h) < min_short:
        scale = max(scale, min_short / float(min(w, h)))
    if scale <= 1.001:
        return w, h
    aspect = w / float(h)
    if h >= w:
        nh = _snap_dim(int(round(h * scale)))
        nw = _snap_dim(max(16, int(round(nh * aspect))))
    else:
        nw = _snap_dim(int(round(w * scale)))
        nh = _snap_dim(max(16, int(round(nw / aspect))))
    return max(64, nw), max(64, nh)


def _size_matches_orientation(size: str, width: int, height: int) -> bool:
    """环境变量覆盖不得把竖屏项目改成横屏 size（反之亦然）。"""
    try:
        a, _, b = size.lower().partition("x")
        sw, sh = int(a), int(b)
    except ValueError:
        return False
    want_portrait = int(height) > int(width)
    got_portrait = sh > sw
    return want_portrait == got_portrait


def _portrait_api_size(width: int, height: int) -> str:
    """上游 size：默认与项目分镜画布同比例；环境变量仅在同方向时允许覆盖。"""
    custom = _env_size("STORYBOARD_IMAGE_SIZE")
    if custom and _size_matches_orientation(custom, width, height):
        return custom
    # 横屏 env（如历史 1536x864）不得覆盖竖屏项目
    w, h = _scale_canvas_to_api_budget(width, height)
    return f"{w}x{h}"


def _api_size_fallbacks(width: int, height: int) -> list[str]:
    """同宽高比优先；最后才落到历史固定档，减少裁切。"""
    primary = _portrait_api_size(width, height)
    exact = f"{_snap_dim(width)}x{_snap_dim(height)}"
    out: list[str] = []
    for s in (primary, exact):
        if s and s not in out and _size_matches_orientation(s, width, height):
            out.append(s)
    # 同比例放大到短边约 1024
    tw, th = _snap_dim(width), _snap_dim(height)
    if min(tw, th) > 0:
        scale = 1024 / float(min(tw, th))
        if scale > 1.01:
            if th >= tw:
                nh = _snap_dim(int(round(th * scale)))
                nw = _snap_dim(int(round(nh * (tw / float(th)))))
            else:
                nw = _snap_dim(int(round(tw * scale)))
                nh = _snap_dim(int(round(nw * (th / float(tw)))))
            same = f"{nw}x{nh}"
            if same not in out:
                out.append(same)
    if th >= tw:
        for s in (
            _env_size("STORYBOARD_IMAGE_SIZE_PORTRAIT"),
            "1024x1792",
            "1024x1536",
        ):
            if s and s not in out:
                out.append(s)
    else:
        for s in (
            _env_size("STORYBOARD_IMAGE_SIZE_LANDSCAPE"),
            "1792x1024",
            "1536x864",
        ):
            if s and s not in out:
                out.append(s)
    return out


def _merge_prompt(prompt: str, negative: str) -> str:
    p = (prompt or "").strip()
    n = (negative or "").strip()
    if not n:
        return p
    if "避免" in p or "negative" in p.lower():
        return p
    return f"{p}\n\nAvoid: {n}"


def _default_image_timeout() -> float:
    raw = (os.environ.get("STORYBOARD_IMAGE_TIMEOUT") or "").strip()
    if raw:
        try:
            return max(60.0, float(raw))
        except ValueError:
            pass
    return 480.0


def generate_still_to_file(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    dest: Path,
    *,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Path:
    """调用 OhMyGPT 等 OpenAI 兼容文生图，写入 dest（PNG）。"""
    base, key = _resolve_api_credentials()
    if not key:
        raise RuntimeError(
            "未配置 OhMyGPT：请在制作台「全局制作 → OhMyGPT 密钥」保存 API Key"
        )
    model = model or image_api_model()
    timeout = _default_image_timeout() if timeout is None else float(timeout)
    url = f"{base}/images/generations"
    if not url.startswith("http"):
        url = f"https://{url.lstrip('/')}"
    size_fallbacks = _api_size_fallbacks(width, height)
    if not size_fallbacks:
        size_fallbacks = [f"{_snap_dim(width)}x{_snap_dim(height)}"]
    # 硬校验：竖屏项目绝不能拿横屏 size 去打上游（历史 systemd 曾写死 1536x864）
    size_fallbacks = [
        s for s in size_fallbacks if _size_matches_orientation(s, width, height)
    ] or [f"{_snap_dim(width)}x{_snap_dim(height)}"]
    size = size_fallbacks[0]
    fmt = (os.environ.get("STORYBOARD_IMAGE_RESPONSE") or "b64_json").strip()

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    proxy = (os.environ.get("STORYBOARD_IMAGE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY") or "").strip()
    client_kw: dict = {"timeout": httpx.Timeout(timeout, connect=min(60.0, timeout))}
    if proxy:
        client_kw["proxy"] = proxy
    last_err: Optional[Exception] = None
    data = None
    attempts = 3
    used_size = size
    for size_try in size_fallbacks:
        used_size = size_try
        body = {
            "model": model,
            "prompt": _merge_prompt(prompt, negative)[:4000],
            "n": 1,
            "size": size_try,
        }
        if fmt:
            body["response_format"] = fmt
        for attempt in range(attempts):
            try:
                with httpx.Client(**client_kw) as client:
                    resp = client.post(url, json=body, headers=headers)
                    if resp.status_code >= 400:
                        # 尺寸不被支持时换下一档，不空耗重试
                        if resp.status_code == 400 and "size" in (resp.text or "").lower():
                            last_err = RuntimeError(
                                f"出图 API HTTP {resp.status_code}: {resp.text[:500]}"
                            )
                            break
                        raise RuntimeError(
                            f"出图 API HTTP {resp.status_code}: {resp.text[:500]}"
                        )
                    data = resp.json()
                break
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                last_err = exc
                if attempt >= attempts - 1:
                    raise RuntimeError(
                        f"出图 API 超时/断连（model={model}, size={size_try}, "
                        f"单次等待{int(timeout)}秒×{attempts}次）。上游未返回图片：{exc}"
                    ) from exc
                time.sleep(2.0 * (attempt + 1))
        if data is not None:
            break
    size = used_size
    if data is None and last_err:
        raise RuntimeError(
            f"出图 API 失败（model={model}, size={size}）：{last_err}"
        ) from last_err

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
