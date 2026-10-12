# -*- coding: utf-8 -*-
"""MiniMax 官方 H3 / H3-Max 视频 API（异步：创建 → 轮询 → 下载）。"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import httpx

ProgressCb = Optional[Callable[[str], None]]


def _settings():
    from . import studio_settings as ss

    return ss


def api_base() -> str:
    return _settings().minimax_api_base()


def api_key() -> str:
    return _settings().minimax_api_key()


def model_name() -> str:
    return _settings().minimax_model()


def resolution() -> str:
    return _settings().minimax_resolution()


def ready() -> bool:
    """官方 API 已彻底关闭。"""
    return False


def _assert_official_api_allowed() -> None:
    from .studio_settings import MINIMAX_OFFICIAL_API_DISABLED

    if MINIMAX_OFFICIAL_API_DISABLED or not api_key():
        raise RuntimeError(
            "官方 MiniMax API 已彻底关闭。视频请走云 GPU ComfyUI（backend=comfy）。"
        )


def _headers() -> Dict[str, str]:
    _assert_official_api_allowed()
    return {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }


def _auth_only() -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_key()}"}


# MiniMax 参考音频合法区间约 [2000, 15000] ms；MP3 帧对齐易略超 15s，故硬顶 14.85s
REF_AUDIO_MAX_SEC = 14.85


def clamp_duration(seconds: float, *, model: Optional[str] = None) -> int:
    m = (model or model_name()).strip()
    d = int(round(float(seconds or 6)))
    if m == "MiniMax-H3-Max":
        return max(5, min(15, d))
    return max(4, min(15, d))


def _enforce_ref_audio_max_sec(path: Path, *, max_sec: float = REF_AUDIO_MAX_SEC) -> Path:
    """强制参考音频 ≤ max_sec，避免上传后被 MiniMax 以 2013 拒收。"""
    import subprocess

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    capped = max(2.0, min(float(max_sec), REF_AUDIO_MAX_SEC))
    tmp = path.with_name(path.stem + ".trim.mp3")
    try:
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-t",
                f"{capped:.3f}",
                "-f",
                "mp3",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(tmp),
            ],
            timeout=60,
        )
        if not tmp.is_file() or tmp.stat().st_size < 128:
            raise RuntimeError("参考音频时长裁剪失败")
        tmp.replace(path)
    finally:
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def upload_video_input(path: Path, *, timeout: float = 120.0) -> str:
    """上传首帧/参考素材，返回 mm_file://{file_id}。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    with path.open("rb") as f:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{api_base()}/v1/files/upload",
                headers=_auth_only(),
                data={"purpose": "video_generation_input"},
                files={"file": (path.name, f, "application/octet-stream")},
            )
    if r.status_code >= 400:
        raise RuntimeError(f"MiniMax 上传失败 HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    base = data.get("base_resp") or {}
    if int(base.get("status_code") or 0) != 0:
        raise RuntimeError(
            f"MiniMax 上传失败: {base.get('status_msg') or data}"[:800]
        )
    file_obj = data.get("file") or {}
    fid = file_obj.get("file_id")
    if fid is None:
        raise RuntimeError(f"MiniMax 上传无 file_id: {data}"[:800])
    return f"mm_file://{fid}"


def _voices_with_text(voices: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for v in voices or []:
        if not isinstance(v, dict):
            continue
        text = (v.get("text") or "").strip()
        if len(text) < 1:
            continue
        out.append(v)
    return out


def _brief_visual(visual: str, *, max_len: int = 160) -> str:
    """去掉冗长禁令/避免列表，只留场景主体。"""
    import re

    v = (visual or "").strip()
    v = re.split(r"(?:避免\s*[：:]|Avoid\s*:)", v, maxsplit=1)[0]
    v = re.sub(r"绝对禁止[^。；;\n]*[。；;\n]?", "", v)
    v = re.sub(r"禁止[^，。；,\n]{0,48}[，。；,\n]?", "", v)
    v = re.sub(r"\s+", " ", v).strip(" ，,。.;")
    if len(v) > max_len:
        v = v[: max_len - 1].rstrip() + "…"
    return v


def _brief_motion(motion: str, *, max_len: int = 140) -> str:
    import re

    m = (motion or "").strip()
    m = re.sub(r"\bno [a-z0-9 \-]{2,40}", "", m, flags=re.I)
    m = re.sub(r"\s+", " ", m).strip(" ,.;")
    if len(m) > max_len:
        m = m[: max_len - 1].rstrip() + "…"
    return m


def build_compact_shot_prompt(
    *,
    shot: Optional[Dict[str, Any]] = None,
    drama_block: str = "",
    voices: Optional[Sequence[Dict[str, Any]]] = None,
    ambience: str = "",
    with_ref_audio: bool = False,
) -> str:
    """H3 短提示词：音轨置顶、画面极短，避免 Drama10 长文淹没旁白。"""
    shot = shot or {}
    voices = _voices_with_text(voices if voices is not None else shot.get("voices"))
    try:
        from .shot_audio_mux import _sanitize_ambience_text

        amb = _sanitize_ambience_text(
            ambience or shot.get("ambience") or shot.get("sound") or ""
        )
    except Exception:
        amb = (ambience or shot.get("ambience") or shot.get("sound") or "").strip()
    if not amb:
        amb = "quiet open office room tone, soft air, distant soft keyboard clicks"
    visual = _brief_visual(shot.get("visual") or "")
    motion = _brief_motion(shot.get("motion") or shot.get("camera") or "")

    lines: List[str] = [
        # 官方写法：结构英文，台词保留中文
        "Soundtrack (required, clear and audible):",
    ]
    narr = False
    if not voices:
        lines.append("- Ambience only; no dialogue. Keep lips closed.")
    for v in voices:
        role = (v.get("role") or "旁白").strip()
        text = (v.get("text") or "").strip()
        try:
            st = float(v.get("start_sec") if v.get("start_sec") is not None else 0.6)
        except (TypeError, ValueError):
            st = 0.6
        if role in ("旁白", "OS", "os", "narrator", "Narrator"):
            narr = True
            lines.append(
                f'- A young Chinese woman says in an off-screen voiceover in Chinese '
                f'starting near {st:.1f}s: "{text}" '
                "while all on-screen characters keep lips completely closed."
            )
        else:
            lines.append(
                f'- {role} speaks on-screen in Chinese starting near {st:.1f}s: "{text}" '
                "with natural lip sync; other characters stay silent."
            )

    lines.append(
        f"- Continuous ambient bed under the speech: {amb}. "
        "Speech louder than ambience. Natural room air only; "
        "no electrical hum, no synthetic drone, no HVAC buzz."
    )
    if voices:
        lines.append(
            "- Speak clearly at a natural, unhurried Chinese pace "
            "(about 3 to 3.5 characters per second). "
            "Leave short pauses between lines; do not rush, overlap, or cram dialogue."
        )
    if with_ref_audio and voices:
        lines.append(
            "- Follow reference Audio 1 for spoken lines and voice timbre; "
            "keep the ambient bed present for the full duration."
        )
    else:
        lines.append(
            "- Generate the spoken Chinese lines and ambience in the video audio track; "
            "do not output silent video or ambience-only when dialogue is specified."
        )

    lines.append("Visual:")
    if visual:
        lines.append(f"- {visual}")
    else:
        lines.append("- Follow the reference image composition exactly.")
    lines.append(
        "- Vertical Chinese manhua anime style, cold office light, "
        "exact match to the reference frame identity and framing."
    )

    lines.append("Camera:")
    if motion:
        lines.append(f"- {motion}")
    else:
        lines.append("- Slow push-in, subtle natural micro-motion, smooth animation.")

    lines.append("Hard rules:")
    lines.append(
        "- Portrait 9:16 only (height > width). No landscape 16:9. "
        "Animate from the reference image as a single continuous shot. "
        "No subtitles, captions, watermark, logo, or on-screen UI text."
    )
    if narr:
        lines.append(
            "- Off-screen voiceover only: lips stay closed for the whole shot."
        )

    # drama_block 故意不拼接：长 Drama10 正是「淹没」主因
    _ = drama_block
    text = "\n".join(lines).strip()
    if len(text) > 7000:
        text = text[:6997] + "…"
    return text


def _voice_for_line(v: Dict[str, Any], *, project_id: Optional[str]) -> Optional[str]:
    from .drama10_voice import OPENAI_TTS_VOICES

    tv = (v.get("tts_voice") or "").strip().lower()
    if tv in OPENAI_TTS_VOICES:
        return tv
    return None


def ratio_from_still(path: Path) -> str:
    """按分镜图方向锁比例，避免 Ref2VA 出横屏。"""
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
        if h > w * 1.05:
            return "9:16"
        if w > h * 1.05:
            return "16:9"
        return "1:1"
    except Exception:
        return "9:16"


def _mix_speech_ambience_mp3(
    speech: Path,
    dest: Path,
    *,
    ambience: str,
    duration_sec: float,
    seed: int = 17,
) -> Path:
    """旁白 + 环境床混成一条参考音频，避免成片只剩干声。"""
    import os
    import subprocess

    from .drama10_ambience import write_ambience_wav

    speech = Path(speech)
    dest = Path(dest)
    with tempfile.TemporaryDirectory(prefix="mm_ambmix_") as td:
        tdir = Path(td)
        amb = tdir / "amb.wav"
        write_ambience_wav(
            amb,
            ambience or "quiet open office room tone, soft air",
            max(float(duration_sec), 5.0) + 0.3,
            seed=seed,
        )
        speech_gain = float(os.environ.get("DRAMA10_REF_SPEECH_GAIN", "1.12"))
        # 参考音频里环境床宜轻，过重会被 H3 学成持续嗡/电流感
        amb_gain = float(os.environ.get("DRAMA10_REF_AMB_GAIN", "0.22"))
        mixed = tdir / "mix.wav"
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(speech),
                "-i",
                str(amb),
                "-filter_complex",
                f"[0:a]volume={speech_gain},aformat=sample_rates=44100:channel_layouts=stereo[s];"
                f"[1:a]volume={amb_gain},aformat=sample_rates=44100:channel_layouts=stereo[a];"
                f"[s][a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
                f"alimiter=limit=0.97[aout]",
                "-map",
                "[aout]",
                str(mixed),
            ],
            timeout=120,
        )
        # 顶到 15.0 时 lame 帧填充常变成 ~15047ms，会被官方拒收
        trim_sec = max(2.0, min(REF_AUDIO_MAX_SEC, float(duration_sec)))
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mixed),
                "-t",
                f"{trim_sec:.3f}",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(dest),
            ],
            timeout=60,
        )
    if not dest.is_file() or dest.stat().st_size < 128:
        raise RuntimeError("旁白+环境参考音频合成失败")
    return _enforce_ref_audio_max_sec(dest)


def _synth_voice_ref_mp3(
    voices: Sequence[Dict[str, Any]],
    dest: Path,
    *,
    project_id: Optional[str],
    ambience: str = "",
    duration_sec: float = 7.0,
    seed: int = 17,
) -> Path:
    """对白 TTS（年轻女声优先）+ 环境床 → 一条 Ref2VA 参考音频。"""
    from .drama10_tts import synthesize_speech_file

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    parts = _voices_with_text(voices)
    if not parts:
        raise ValueError("无对白可合成参考音频")

    import subprocess

    with tempfile.TemporaryDirectory(prefix="mm_tts_") as td:
        tdir = Path(td)
        segs: List[Path] = []
        for i, v in enumerate(parts):
            raw = tdir / f"raw_{i}.mp3"
            synthesize_speech_file(
                raw,
                (v.get("text") or "").strip(),
                (v.get("role") or "旁白").strip(),
                project_id=project_id,
                voice=_voice_for_line(v, project_id=project_id),
            )
            try:
                st = float(v.get("start_sec") if v.get("start_sec") is not None else 0.6)
            except (TypeError, ValueError):
                st = 0.6
            delay_ms = max(0, int(st * 1000))
            seg = tdir / f"seg_{i}.wav"
            subprocess.check_call(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(raw),
                    "-af",
                    f"adelay={delay_ms}|{delay_ms}",
                    str(seg),
                ],
                timeout=60,
            )
            segs.append(seg)
        speech_only = tdir / "speech.mp3"
        if len(segs) == 1:
            subprocess.check_call(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(segs[0]),
                    "-c:a",
                    "libmp3lame",
                    "-q:a",
                    "4",
                    str(speech_only),
                ],
                timeout=60,
            )
        else:
            ff_in: List[str] = []
            for p in segs:
                ff_in.extend(["-i", str(p)])
            filt = (
                "".join(f"[{i}:a]" for i in range(len(segs)))
                + f"amix=inputs={len(segs)}:duration=longest:normalize=0[aout]"
            )
            subprocess.check_call(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    *ff_in,
                    "-filter_complex",
                    filt,
                    "-map",
                    "[aout]",
                    "-c:a",
                    "libmp3lame",
                    "-q:a",
                    "4",
                    str(speech_only),
                ],
                timeout=120,
            )
        _mix_speech_ambience_mp3(
            speech_only,
            dest,
            ambience=ambience,
            duration_sec=min(float(duration_sec), REF_AUDIO_MAX_SEC),
            seed=seed,
        )
    if not dest.is_file() or dest.stat().st_size < 128:
        raise RuntimeError("参考音频合成失败")
    return _enforce_ref_audio_max_sec(dest)


def _friendly_minimax_http_error(status_code: int, body: str) -> str:
    text = (body or "")[:800]
    low = text.lower()
    if status_code == 402 or "insufficient_balance" in low or "insufficient balance" in low:
        return (
            "MiniMax 账户余额不足，无法创建视频任务。"
            "请登录 MiniMax 控制台充值后再点「批量生成本集视频」。"
        )
    if status_code in (401, 403) or "invalid api" in low or "unauthorized" in low:
        return "MiniMax API Key 无效或无权限，请在「制作设置」检查密钥。"
    if status_code == 429 or "rate limit" in low:
        return "MiniMax 请求过于频繁，请稍后再试。"
    return f"MiniMax 创建任务失败 HTTP {status_code}: {text}"


def _friendly_minimax_api_error(status_code: Any, msg: Any) -> str:
    code = str(status_code or "")
    text = str(msg or "")[:800]
    low = text.lower()
    if code in ("1008", "2013") and "balance" in low:
        return "MiniMax 账户余额不足，请充值后再生成视频。"
    if "insufficient" in low and "balance" in low:
        return "MiniMax 账户余额不足，请充值后再生成视频。"
    return f"MiniMax 创建任务失败: {text}"


def create_video_task(
    *,
    prompt: str,
    image_url: str,
    duration: int,
    resolution_name: Optional[str] = None,
    model: Optional[str] = None,
    audio_url: Optional[str] = None,
    ratio: Optional[str] = None,
    timeout: float = 120.0,
    extra_image_urls: Optional[Sequence[str]] = None,
) -> str:
    """提交视频任务。有 audio_url 时走 Ref2VA（reference_image+reference_audio）。

    extra_image_urls：额外参考图（角色/场景/道具），以 reference_image 追加，支持多图提交。
    """
    _assert_official_api_allowed()
    text = (prompt or "").strip()
    if len(text) > 7000:
        text = text[:6997] + "…"
    if not text:
        raise ValueError("prompt 为空")
    res = (resolution_name or resolution() or "768P").strip()
    content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
    extras = [u for u in (extra_image_urls or []) if u and u != image_url]
    if audio_url:
        # 图生视频与多模态参考互斥：有参考音频时图片必须用 reference_image
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_url},
                "role": "reference_image",
            }
        )
        for u in extras[:8]:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": u},
                    "role": "reference_image",
                }
            )
        content.append(
            {
                "type": "audio_url",
                "audio_url": {"url": audio_url},
                "role": "reference_audio",
            }
        )
    else:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_url},
                "role": "first_frame",
            }
        )
        for u in extras[:8]:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": u},
                    "role": "reference_image",
                }
            )
    payload: Dict[str, Any] = {
        "model": (model or model_name()).strip(),
        "content": content,
        "duration": int(duration),
        "resolution": res,
        "extra": {"prompt_expansion_mode": "quality"},
    }
    # Ref2VA 默认 adaptive 易出横屏；竖屏分镜必须显式 9:16
    r = (ratio or "").strip()
    if audio_url:
        payload["ratio"] = r or "9:16"
    elif r and r != "adaptive":
        payload["ratio"] = r
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{api_base()}/v2/video_generation",
            headers=_headers(),
            json=payload,
        )
    if r.status_code >= 400:
        raise RuntimeError(_friendly_minimax_http_error(r.status_code, r.text))
    data = r.json()
    base = data.get("base_resp") or {}
    if base and int(base.get("status_code") or 0) != 0:
        raise RuntimeError(
            _friendly_minimax_api_error(base.get("status_code"), base.get("status_msg") or data)
        )
    tid = data.get("task_id")
    if not tid:
        raise RuntimeError(f"MiniMax 未返回 task_id: {data}"[:800])
    return str(tid)


# 兼容旧名
def create_image_to_video(
    *,
    prompt: str,
    image_url: str,
    duration: int,
    resolution_name: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 120.0,
) -> str:
    return create_video_task(
        prompt=prompt,
        image_url=image_url,
        duration=duration,
        resolution_name=resolution_name,
        model=model,
        timeout=timeout,
    )


def query_task(task_id: str, *, timeout: float = 60.0) -> Dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        r = client.get(
            f"{api_base()}/v2/query/video_generation/{task_id}",
            headers=_headers(),
        )
    if r.status_code >= 400:
        raise RuntimeError(f"MiniMax 查询失败 HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    task = data.get("task")
    if isinstance(task, dict):
        return task
    if "status" in data:
        return data
    raise RuntimeError(f"MiniMax 查询响应异常: {data}"[:800])


def wait_video_url(
    task_id: str,
    *,
    poll_sec: float = 10.0,
    timeout: float = 3600.0,
    on_progress: ProgressCb = None,
) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        task = query_task(task_id)
        status = str(task.get("status") or "").lower()
        if on_progress:
            on_progress(f"MiniMax 任务 {task_id[:8]}… · {status or '?'}")
        if status in ("succeeded", "success", "completed"):
            content = task.get("content") or {}
            url = content.get("url") if isinstance(content, dict) else None
            if not url:
                raise RuntimeError(f"任务成功但无 content.url: {task}"[:800])
            return str(url)
        if status in ("failed", "cancelled", "canceled", "error"):
            err = task.get("error") or task.get("message") or task
            raise RuntimeError(f"MiniMax 生成失败: {err}"[:800])
        time.sleep(max(3.0, float(poll_sec)))
    raise TimeoutError(f"MiniMax 任务超时: {task_id}")


def download_file(url: str, dest: Path, *, timeout: float = 300.0) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


def _sanitize_amb_for_h3(text: str) -> str:
    from .shot_audio_mux import _sanitize_ambience_text

    return _sanitize_ambience_text(text or "")


def polish_h3_mp4_audio(
    mp4: Path,
    *,
    ambience: str = "",
    seed: int = 17,
) -> Path:
    """保留 H3 原声：默认只去电源嗡，不再叠本地合成环境床（叠噪易听成电流声）。

    环境声已在 Ref2VA 参考音频 / H3 成片里；若仍要叠本地床：
    DRAMA10_H3_POLISH_ADD_AMB=1
    """
    import os
    import subprocess

    mp4 = Path(mp4)
    if not mp4.is_file():
        raise FileNotFoundError(str(mp4))
    speech_gain = float(os.environ.get("DRAMA10_H3_SPEECH_GAIN", "1.08"))
    add_amb = (os.environ.get("DRAMA10_H3_POLISH_ADD_AMB") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    # 陷波 50/60/100/120Hz + 轻高通，去掉电流感同时尽量留人声
    dehum_af = (
        os.environ.get("DRAMA10_H3_DEHUM_AF")
        or "highpass=f=80,equalizer=f=50:width_type=h:width=35:g=-22,"
        "equalizer=f=60:width_type=h:width=35:g=-22,"
        "equalizer=f=100:width_type=h:width=45:g=-14,"
        "equalizer=f=120:width_type=h:width=45:g=-14,"
        "equalizer=f=240:width_type=h:width=60:g=-6"
    )

    with tempfile.TemporaryDirectory(prefix="mm_polish_") as td:
        tdir = Path(td)
        cleaned = tdir / "cleaned.wav"
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mp4),
                "-vn",
                "-af",
                f"{dehum_af},volume={speech_gain},alimiter=limit=0.97",
                "-ac",
                "2",
                "-ar",
                "44100",
                str(cleaned),
            ],
            timeout=120,
        )
        audio_for_mux = cleaned
        if add_amb:
            from .drama10_ambience import write_ambience_wav

            amb_desc = _sanitize_amb_for_h3(ambience) or (
                "quiet open office room tone, soft air, distant soft keyboard clicks"
            )
            amb_gain = float(os.environ.get("DRAMA10_H3_AMB_GAIN", "0.28"))
            dur_out = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(mp4),
                ],
                text=True,
                timeout=20,
            )
            dur = max(1.0, float((dur_out or "7").strip() or "7"))
            amb = tdir / "amb.wav"
            write_ambience_wav(amb, amb_desc, dur + 0.4, seed=seed)
            mix = tdir / "mix.wav"
            subprocess.check_call(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(cleaned),
                    "-i",
                    str(amb),
                    "-filter_complex",
                    f"[0:a]volume=1.0[s];"
                    f"[1:a]volume={amb_gain}[a];"
                    f"[s][a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                    f"alimiter=limit=0.97[aout]",
                    "-map",
                    "[aout]",
                    str(mix),
                ],
                timeout=120,
            )
            audio_for_mux = mix

        video_only = tdir / "v.mp4"
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
                str(video_only),
            ],
            timeout=120,
        )
        out = tdir / "out.mp4"
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video_only),
                "-i",
                str(audio_for_mux),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "256k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(out),
            ],
            timeout=120,
        )
        mp4.write_bytes(out.read_bytes())
    return mp4


def _want_ref_audio() -> bool:
    """默认关闭本地 TTS 参考音频：叠在 H3 自带对白上会多重乱声。

    仅当环境变量 MINIMAX_USE_REF_AUDIO=1 时才走 Ref2VA（工作台无此音频资产）。
    """
    import os

    return (os.environ.get("MINIMAX_USE_REF_AUDIO") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def generate_shot_i2v(
    *,
    still_png: Path,
    prompt: str,
    duration_sec: float,
    dest_mp4: Path,
    on_progress: ProgressCb = None,
    shot: Optional[Dict[str, Any]] = None,
    project_id: Optional[str] = None,
    with_speech: bool = True,
    extra_ref_images: Optional[Sequence[Path]] = None,
) -> Path:
    """图生视频：默认只传参考图 + 台词提示，由 MiniMax 一次生成成片音轨。

    不再默认上传本地 TTS/环境参考音频（工作台也没有这类音频资产）。
    extra_ref_images：额外本地参考图路径（多图提交）。
    """
    _assert_official_api_allowed()
    shot = shot or {}
    voices = _voices_with_text(shot.get("voices"))
    if not voices and shot.get("voices_json"):
        try:
            voices = _voices_with_text(json.loads(shot.get("voices_json") or "[]"))
        except json.JSONDecodeError:
            voices = []

    # 默认 False：只让 MiniMax 出声，避免「本地 TTS 参考 + 模型自带对白」叠成多重声
    use_ref_audio = bool(with_speech and voices and _want_ref_audio())
    amb = _sanitize_amb_for_h3(
        (shot.get("ambience") or shot.get("sound") or "").strip()
    )
    compact = build_compact_shot_prompt(
        shot=shot,
        drama_block=prompt,
        voices=voices,
        ambience=amb,
        with_ref_audio=use_ref_audio,
    )
    aspect = ratio_from_still(Path(still_png))

    if on_progress:
        on_progress(f"MiniMax · 上传参考图（目标 {aspect}）…")
    image_ref = upload_video_input(still_png)
    extra_refs: List[str] = []
    for p in list(extra_ref_images or [])[:8]:
        pp = Path(p)
        if not pp.is_file():
            continue
        if on_progress:
            on_progress(f"MiniMax · 上传附加参考图 {pp.name}…")
        try:
            extra_refs.append(upload_video_input(pp))
        except Exception as e:
            if on_progress:
                on_progress(f"MiniMax · 附加图跳过：{e}"[:100])
    dur = clamp_duration(duration_sec)

    if use_ref_audio:
        if on_progress:
            on_progress("MiniMax · 合成旁白+环境参考音频…")
        with tempfile.TemporaryDirectory(prefix="mm_vo_") as td:
            tts_path = Path(td) / "voice_ref.mp3"
            _synth_voice_ref_mp3(
                voices,
                tts_path,
                project_id=project_id,
                ambience=amb,
                duration_sec=min(float(dur), REF_AUDIO_MAX_SEC),
                seed=int(shot.get("shot_num") or shot.get("id") or 1) * 17,
            )
            _enforce_ref_audio_max_sec(tts_path)
            if on_progress:
                on_progress("MiniMax · 上传参考音频…")
            audio_ref = upload_video_input(tts_path)
            if on_progress:
                on_progress(
                    f"MiniMax · 提交 Ref2VA（{dur}s · {resolution()} · {aspect}）…"
                )
            try:
                tid = create_video_task(
                    prompt=compact,
                    image_url=image_ref,
                    duration=dur,
                    audio_url=audio_ref,
                    ratio=aspect,
                    extra_image_urls=extra_refs,
                )
            except Exception as e:
                if on_progress:
                    on_progress(f"MiniMax · Ref2VA 不可用，改首帧图生：{e}"[:120])
                compact = build_compact_shot_prompt(
                    shot=shot,
                    drama_block=prompt,
                    voices=voices,
                    ambience=amb,
                    with_ref_audio=False,
                )
                tid = create_video_task(
                    prompt=compact,
                    image_url=image_ref,
                    duration=dur,
                    audio_url=None,
                    ratio=aspect,
                    extra_image_urls=extra_refs,
                )
    else:
        if on_progress:
            n_extra = len(extra_refs)
            extra_note = f" · 附加{n_extra}图" if n_extra else ""
            voice_note = " · 模型自带音轨" if with_speech and voices else ""
            on_progress(
                f"MiniMax · 提交图生视频（{dur}s · {resolution()} · {aspect}{extra_note}{voice_note}）…"
            )
        tid = create_video_task(
            prompt=compact or (prompt or "").strip(),
            image_url=image_ref,
            duration=dur,
            audio_url=None,
            ratio=aspect,
            extra_image_urls=extra_refs,
        )

    url = wait_video_url(tid, on_progress=on_progress)
    if on_progress:
        on_progress("MiniMax · 下载成片…")
    download_file(url, dest_mp4)
    if with_speech:
        if on_progress:
            on_progress("MiniMax · 轻量去嗡（保留模型原声）…")
        polish_h3_mp4_audio(
            Path(dest_mp4),
            ambience=amb,
            seed=int(shot.get("shot_num") or shot.get("id") or 1) * 17,
        )
    return Path(dest_mp4)
