# -*- coding: utf-8
"""单镜 MP4 补混音（Comfy 未写入音轨时的兜底：TTS + 简易 BGM + ffmpeg mux）。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db
from .drama10_ambience import write_ambience_wav
from .drama10_crowd import is_crowd_role, render_crowd_speech_wav
from .drama10_export import extract_shot_blocks


def _mp4_mean_volume_db(path: Path) -> Optional[float]:
    """volumedetect mean_volume；None 表示无音轨或检测失败。"""
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
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
            timeout=45,
        )
        m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", proc.stderr or "")
        if m:
            return float(m.group(1))
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    return None


def _needs_audio_remux(path: Path) -> bool:
    if not _mp4_has_audio(path):
        return True
    db = _mp4_mean_volume_db(path)
    if db is None:
        return True
    thr = float(os.environ.get("DRAMA10_SILENT_MEAN_DB", "-42"))
    return db < thr


def _mp4_has_audio(path: Path) -> bool:
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
            timeout=25,
        )
        return b"audio" in out.lower()
    except (subprocess.SubprocessError, OSError, ValueError):
        return False


def _probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        timeout=20,
    )
    return max(0.5, float(out.strip() or "6"))


def _polish_speech_wav(path: Path) -> None:
    """仅处理人声，避免把风雪/旗声当噪声消掉。"""
    tmp = path.with_suffix(".polish.wav")
    af = (
        os.environ.get("DRAMA10_SPEECH_AF") or "highpass=f=120,lowpass=11500"
    ).strip()
    try:
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-af",
                af,
                str(tmp),
            ],
            timeout=120,
        )
        if tmp.is_file() and tmp.stat().st_size > 256:
            tmp.replace(path)
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)


def _master_mix_wav(path: Path) -> None:
    """成片轻量限幅，保留环境低频。"""
    tmp = path.with_suffix(".master.wav")
    try:
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-af",
                "highpass=f=200,alimiter=limit=0.95",
                str(tmp),
            ],
            timeout=120,
        )
        if tmp.is_file() and tmp.stat().st_size > 256:
            tmp.replace(path)
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)


def _run_tts(text: str, role: str, dest: Path, *, project_id: str) -> None:
    from .drama10_tts import synthesize_speech_file

    synthesize_speech_file(dest, text, role, project_id=project_id)


def _voices_from_shot(shot: Dict[str, Any], prompt_block: str) -> List[Dict[str, Any]]:
    raw = shot.get("voices")
    if raw is None:
        try:
            raw = json.loads(shot.get("voices_json") or "[]")
        except json.JSONDecodeError:
            raw = []
    if isinstance(raw, list) and raw:
        return raw
    out: List[Dict[str, Any]] = []
    for m in re.finditer(
        r"旁白\s*[：:]\s*「(.+?)」(?:\s*[（(]([\d.]+)\s*秒)?",
        prompt_block,
        re.S,
    ):
        item: Dict[str, Any] = {"role": "旁白", "text": m.group(1).strip()}
        if m.group(2):
            item["start_sec"] = float(m.group(2))
        out.append(item)
    return out


def _sanitize_ambience_text(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"\bepic\s+drone\b", "soft wind air", t, flags=re.I)
    t = re.sub(r"\bdrone\b", "wind bed", t, flags=re.I)
    t = re.sub(r"\brumble\b", "room tone", t, flags=re.I)
    t = re.sub(r"\blow\s+rumble\b", "soft room tone", t, flags=re.I)
    t = re.sub(r"电流|嗡鸣|合成器|低频嗡", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _ambience_from_shot(shot: Dict[str, Any], prompt_block: str) -> str:
    parts: List[str] = []
    for key in ("ambience", "sound", "sfx"):
        v = (shot.get(key) or "").strip()
        if v:
            parts.append(v)
    amb = _sanitize_ambience_text("；".join(parts))
    if amb:
        return amb[:320]
    m = re.search(
        r"(?:^|\n)\s*声音\s*[：:]\s*(.+?)(?=\n\s*(?:旁白|对白|视频提示词)|\Z)",
        prompt_block,
        re.S,
    )
    if m:
        return _sanitize_ambience_text(m.group(1).strip())[:320]
    default = (
        os.environ.get("DRAMA10_DEFAULT_AMBIENCE")
        or "wind air cinematic atmosphere, soft snow room tone, continuous bed"
    )
    return _sanitize_ambience_text(default)


def mux_shot_mp4_if_silent(
    project_id: str,
    episode_id: str,
    shot_num: int,
    *,
    force: bool = False,
) -> bool:
    """为 shot_XX.mp4 写入 Web 混音（TTS+环境+BGM），丢弃原 H3/Comfy 音轨。"""
    from .services import _build_drama10_prompt, get_episode

    ep = get_episode(episode_id)
    ep_num = int(ep["ep_num"])
    sn = int(shot_num)
    base = db.project_dir(project_id)
    mp4 = base / "episodes" / f"ep{ep_num:02d}" / "shots" / f"shot_{sn:02d}.mp4"
    if not mp4.is_file():
        return False
    if not force and not _needs_audio_remux(mp4):
        return True

    shot = next((s for s in ep.get("shots") or [] if int(s["shot_num"]) == sn), None)
    if not shot:
        raise ValueError(f"找不到镜 {sn}")

    prompt = _build_drama10_prompt(project_id, episode_id, shot_nums=[sn], for_video=True)
    blocks = extract_shot_blocks(prompt)
    block = blocks.get(sn) or ""
    voices = _voices_from_shot(shot, block)
    ambience = _ambience_from_shot(shot, block)
    dur = _probe_duration(mp4)
    print(
        f"[shot_audio_mux] shot={sn} dur={dur:.1f}s voices={len(voices)} ambience={ambience[:80]!r}",
        flush=True,
    )

    with tempfile.TemporaryDirectory(prefix="bingjing_mux_") as td:
        tdir = Path(td)
        video_only = tdir / "video_only.mp4"
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
        speech_parts: List[Path] = []

        for i, v in enumerate(voices):
            text = (v.get("text") or "").strip()
            if len(text) < 1:
                continue
            role = (v.get("role") or "旁白").strip()
            st = float(v.get("start_sec") or 0.8)
            seg = tdir / f"seg_{i}.wav"
            if is_crowd_role(role):
                render_crowd_speech_wav(
                    project_id,
                    text,
                    seg,
                    start_sec=st,
                    work_dir=tdir / f"crowd_{i}",
                    duration_hint=dur,
                )
                _polish_speech_wav(seg)
            else:
                tts = tdir / f"tts_{i}.mp3"
                _run_tts(text, role, tts, project_id=project_id)
                delay_ms = max(0, int(st * 1000))
                subprocess.check_call(
                    [
                        "ffmpeg",
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(tts),
                        "-af",
                        f"adelay={delay_ms}|{delay_ms},highpass=f=110,lowpass=11000",
                        str(seg),
                    ],
                    timeout=60,
                )
            speech_parts.append(seg)

        speech_mix = tdir / "speech.wav"
        if not ambience:
            ambience = _ambience_from_shot(shot, block)
        if speech_parts:
            if len(speech_parts) == 1:
                speech_parts[0].replace(speech_mix)
            else:
                inputs: List[str] = []
                for p in speech_parts:
                    inputs.extend(["-i", str(p)])
                filt = (
                    "".join(f"[{i}:a]" for i in range(len(speech_parts)))
                    + f"amix=inputs={len(speech_parts)}:duration=longest[aout]"
                )
                subprocess.check_call(
                    [
                        "ffmpeg",
                        "-y",
                        "-loglevel",
                        "error",
                        *inputs,
                        "-filter_complex",
                        filt,
                        "-map",
                        "[aout]",
                        str(speech_mix),
                    ],
                    timeout=120,
                )
            _polish_speech_wav(speech_mix)

        timeline = tdir / "mix.wav"
        amb_wav = tdir / "amb.wav"
        write_ambience_wav(amb_wav, ambience, dur + 0.5, seed=sn * 17 + ep_num)
        amb_gain = float(os.environ.get("DRAMA10_AMB_MIX_GAIN", "1.85"))
        speech_gain = float(os.environ.get("DRAMA10_SPEECH_MIX_GAIN", "1.05"))
        layers: List[tuple] = []
        if speech_mix.is_file():
            layers.append((speech_mix, speech_gain))
        layers.append((amb_wav, amb_gain))

        if len(layers) == 1:
            Path(layers[0][0]).replace(timeline)
        else:
            ff_in: List[str] = []
            for p, _ in layers:
                ff_in.extend(["-i", str(p)])
            vols: List[str] = []
            for i, (_, g) in enumerate(layers):
                vols.append(f"[{i}:a]volume={g}[a{i}];")
            n_in = len(layers)
            filt = (
                "".join(vols)
                + "".join(f"[a{i}]" for i in range(n_in))
                + f"amix=inputs={n_in}:duration=longest:dropout_transition=0[aout]"
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
                    str(timeline),
                ],
                timeout=120,
            )

        _master_mix_wav(timeline)
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(timeline),
                "-af",
                "highpass=f=200,lowpass=11000,dynaudnorm=f=120:g=12,volume=1.25,alimiter=limit=0.97",
                str(timeline.with_suffix(".loud.wav")),
            ],
            timeout=60,
        )
        loud = timeline.with_suffix(".loud.wav")
        if loud.is_file() and loud.stat().st_size > 256:
            loud.replace(timeline)
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
                str(timeline),
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
        if not _mp4_has_audio(out):
            raise RuntimeError("补混音后仍无音轨")
        mp4.write_bytes(out.read_bytes())
        db_vol = _mp4_mean_volume_db(mp4)
        print(
            f"[shot_audio_mux] done shot={sn} mean_volume_db={db_vol}",
            flush=True,
        )
    return True


def remux_episode_shots(
    project_id: str,
    episode_id: str,
    *,
    shot_nums: Optional[List[int]] = None,
    force: bool = True,
) -> List[int]:
    """强制为指定镜（默认全集）重写音轨。"""
    from .services import get_episode

    ep = get_episode(episode_id)
    nums = shot_nums or [int(s["shot_num"]) for s in ep.get("shots") or []]
    done: List[int] = []
    for sn in nums:
        mp4 = (
            db.project_dir(project_id)
            / "episodes"
            / f"ep{int(ep['ep_num']):02d}"
            / "shots"
            / f"shot_{int(sn):02d}.mp4"
        )
        if not mp4.is_file():
            continue
        mux_shot_mp4_if_silent(project_id, episode_id, int(sn), force=force)
        done.append(int(sn))
    return done
