# -*- coding: utf-8 -*-
"""分镜静帧：OpenAI 兼容 /v1/images/generations（模型广场、OpenRouter 等）。"""
from __future__ import annotations

import base64
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

import httpx
import time

from .studio_settings import DEFAULT_OHMYGPT_API_BASE, ohmygpt_api_base, ohmygpt_api_key
from .studio_settings import ohmygpt_configured as image_api_configured

BACKEND_IMAGE_API = "image_api"

# 可调用：返回 True 表示任务已取消，应立刻中止 HTTP
CancelCheck = Optional[Callable[[], bool]]
# 可调用：elapsed_sec → 心跳（刷新任务 updated_at，避免界面误判卡死）
TickCallback = Optional[Callable[[float], None]]


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


class StillModerationBlocked(RuntimeError):
    """上游内容安全审核拦截（如 sexual / violence）。"""


def _is_moderation_blocked_text(text: str) -> bool:
    t = (text or "").lower()
    return any(
        k in t
        for k in (
            "moderation_blocked",
            "safety_violations",
            "safety system",
            "moderation_details",
            "content_policy",
        )
    )


def sanitize_still_prompt_for_safety(prompt: str) -> str:
    """把易触发审核的亲密/暴露描写改成 PG-13 电影分镜表述。"""
    import re

    p = (prompt or "").strip()
    repl = (
        (r"亲吻|深吻|热吻|吻住|吻上", "近距离对视、额头轻触"),
        (r"嘴唇相贴|唇贴|嘴对嘴", "两人面部贴近、目光交汇"),
        (r"咬唇|舔唇|舔舐", "轻抿嘴唇"),
        (r"湿润的?[眼唇嘴]|水润", "微光"),
        (r"锁骨|胸线|乳沟|胸部|裸肩", "肩颈线条"),
        (r"吊带睡裙|睡裙|吊带裙|睡衣吊带", "素色长袖居家长裙"),
        (r"薄纱|透视|露肤|裸[露体]", "布料完整遮盖"),
        (r"床单凌乱|滚床单|压在.*身上", "坐在床沿对峙"),
        (r"撕扯衣|扯开衣|解开衣扣", "拉住衣袖对峙"),
        (r"性[感暗示]|暧昧缠绵", "情绪对峙"),
    )
    for pat, to in repl:
        p = re.sub(pat, to, p)
    suffix = (
        "【安全约束】PG-13 cinematic still, fully clothed adults, "
        "no nudity, no sexual content, no lingerie, tasteful Chinese drama framing."
    )
    if "PG-13" not in p:
        p = f"{p}\n{suffix}"
    return p


def moderation_safe_negative(negative: str = "") -> str:
    extra = (
        "nsfw, sexual content, nude, cleavage, lingerie, "
        "seductive pose, explicit, pornography"
    )
    n = (negative or "").strip()
    return f"{n}, {extra}" if n else extra


def _default_image_timeout() -> float:
    """单次 HTTP 读超时。过大时上游挂起会导致界面长时间停在某一镜。"""
    raw = (os.environ.get("STORYBOARD_IMAGE_TIMEOUT") or "").strip()
    if raw:
        try:
            return max(60.0, float(raw))
        except ValueError:
            pass
    return 180.0


def _default_image_attempts() -> int:
    raw = (os.environ.get("STORYBOARD_IMAGE_ATTEMPTS") or "").strip()
    if raw:
        try:
            return max(1, min(4, int(raw)))
        except ValueError:
            pass
    return 2


def _compress_ref_for_api(ref_image: Path) -> Tuple[str, str]:
    """压缩参考图为较小 JPEG，避免巨量 base64 卡死写超时/上游。返回 (mime, b64)。"""
    raw = Path(ref_image).read_bytes()
    mime = "image/jpeg"
    try:
        from PIL import Image
        import io

        im = Image.open(io.BytesIO(raw)).convert("RGB")
        max_edge = 1024
        try:
            max_edge = max(512, min(1536, int(os.environ.get("STORYBOARD_REF_MAX_EDGE") or "1024")))
        except ValueError:
            pass
        im.thumbnail((max_edge, max_edge))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82, optimize=True)
        return mime, base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        # 回退原图（可能较大）
        suffix = Path(ref_image).suffix.lower()
        if suffix in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif suffix == ".webp":
            mime = "image/webp"
        else:
            mime = "image/png"
        return mime, base64.b64encode(raw).decode("ascii")


class StillApiCancelled(RuntimeError):
    """任务取消导致出图中止。"""


def _generate_still_to_file_once(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    dest: Path,
    *,
    model: str,
    timeout: float,
    ref_image: Optional[Path] = None,
    cancel_check: CancelCheck = None,
    on_tick: TickCallback = None,
) -> Path:
    """单次出图尝试（不含审核降级）。"""
    base, key = _resolve_api_credentials()
    if not key:
        raise RuntimeError(
            "未配置 OhMyGPT：请在制作台「全局制作 → OhMyGPT 密钥」保存 API Key"
        )
    attempts = _default_image_attempts()
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
    # 最多试 2 档尺寸，避免长时间空转
    size_fallbacks = size_fallbacks[:2]
    size = size_fallbacks[0]
    fmt = (os.environ.get("STORYBOARD_IMAGE_RESPONSE") or "b64_json").strip()

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    proxy = (
        os.environ.get("STORYBOARD_IMAGE_HTTPS_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or ""
    ).strip()
    # 分开 write/read：大 JSON 参考图上传与上游出图互不拖死
    client_kw: dict = {
        "timeout": httpx.Timeout(
            connect=20.0,
            read=float(timeout),
            write=min(90.0, float(timeout)),
            pool=20.0,
        )
    }
    if proxy:
        client_kw["proxy"] = proxy
    last_err: Optional[Exception] = None
    data = None
    used_size = size
    ref_data_url = ""
    if ref_image and Path(ref_image).is_file():
        mime, ref_b64 = _compress_ref_for_api(Path(ref_image))
        ref_data_url = f"data:{mime};base64,{ref_b64}"

    def _raise_if_cancelled() -> None:
        if cancel_check and cancel_check():
            raise StillApiCancelled("出图已取消")

    for size_try in size_fallbacks:
        _raise_if_cancelled()
        used_size = size_try
        body = {
            "model": model,
            "prompt": _merge_prompt(prompt, negative)[:4000],
            "n": 1,
            "size": size_try,
        }
        if fmt:
            body["response_format"] = fmt
        if ref_data_url:
            body["image"] = ref_data_url
        for attempt in range(attempts):
            _raise_if_cancelled()
            client = httpx.Client(**client_kw)
            stop_watch = threading.Event()
            t0 = time.time()

            def _watcher(
                _client: httpx.Client = client,
                _stop: threading.Event = stop_watch,
                _t0: float = t0,
            ) -> None:
                # 取消 / 硬顶超时：主动 close，打断可能挂起的 post
                hard = float(timeout) + 45.0
                while not _stop.wait(2.0):
                    if cancel_check and cancel_check():
                        try:
                            _client.close()
                        except Exception:
                            pass
                        return
                    if on_tick:
                        try:
                            on_tick(time.time() - _t0)
                        except Exception:
                            pass
                    if time.time() - _t0 > hard:
                        try:
                            _client.close()
                        except Exception:
                            pass
                        return

            th = threading.Thread(target=_watcher, daemon=True)
            th.start()
            try:
                resp = client.post(url, json=body, headers=headers)
                if resp.status_code >= 400:
                    txt = resp.text or ""
                    if _is_moderation_blocked_text(txt):
                        raise StillModerationBlocked(
                            f"出图 API 安全审核拦截：{txt[:400]}"
                        )
                    # 尺寸不被支持时换下一档，不空耗重试
                    if resp.status_code == 400 and "size" in txt.lower():
                        last_err = RuntimeError(
                            f"出图 API HTTP {resp.status_code}: {txt[:500]}"
                        )
                        break
                    # 上游不接受参考图字段时，去掉 image 再试纯文生图
                    if (
                        resp.status_code == 400
                        and ref_data_url
                        and "image" in txt.lower()
                    ):
                        body.pop("image", None)
                        ref_data_url = ""
                        last_err = RuntimeError(txt[:300])
                        continue
                    raise RuntimeError(
                        f"出图 API HTTP {resp.status_code}: {txt[:500]}"
                    )
                data = resp.json()
                break
            except (StillApiCancelled, StillModerationBlocked):
                raise
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if cancel_check and cancel_check():
                    raise StillApiCancelled("出图已取消") from exc
                last_err = exc
                if attempt >= attempts - 1:
                    raise RuntimeError(
                        f"出图 API 超时/断连（model={model}, size={size_try}, "
                        f"单次等待{int(timeout)}秒×{attempts}次）。上游未返回图片：{exc}"
                    ) from exc
                time.sleep(2.0 * (attempt + 1))
            finally:
                stop_watch.set()
                try:
                    client.close()
                except Exception:
                    pass
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
        with httpx.Client(
            timeout=httpx.Timeout(connect=20.0, read=float(timeout), write=60.0, pool=20.0)
        ) as client:
            r = client.get(img_url)
            r.raise_for_status()
            dest.write_bytes(r.content)
        return dest
    raise RuntimeError(f"出图 API 响应无图片数据: {str(data)[:300]}")


def generate_still_to_file(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    dest: Path,
    *,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    ref_image: Optional[Path] = None,
    cancel_check: CancelCheck = None,
    on_tick: TickCallback = None,
) -> Path:
    """调用 OhMyGPT 等 OpenAI 兼容文生图，写入 dest（PNG）。

    若触发安全审核，自动用 PG-13 净化提示词并去掉参考图再试一次。
    """
    model_s = model or image_api_model()
    timeout_f = _default_image_timeout() if timeout is None else float(timeout)
    try:
        return _generate_still_to_file_once(
            prompt,
            negative,
            width,
            height,
            dest,
            model=model_s,
            timeout=timeout_f,
            ref_image=ref_image,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )
    except StillModerationBlocked:
        safe_p = sanitize_still_prompt_for_safety(prompt)
        safe_n = moderation_safe_negative(negative)
        if on_tick:
            try:
                on_tick(0.0)
            except Exception:
                pass
        return _generate_still_to_file_once(
            safe_p,
            safe_n,
            width,
            height,
            dest,
            model=model_s,
            timeout=timeout_f,
            ref_image=None,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )


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
    ref_image: Optional[Path] = None,
    cancel_check: CancelCheck = None,
    on_tick: TickCallback = None,
) -> Path:
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    path = Path(name)
    try:
        return generate_still_to_file(
            prompt,
            negative,
            width,
            height,
            path,
            model=model,
            ref_image=ref_image,
            cancel_check=cancel_check,
            on_tick=on_tick,
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
