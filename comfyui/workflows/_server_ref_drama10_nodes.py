#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短剧10镜 MiniMax H3
- 有拼图：拆镜 → 图生视频 → 统一音轨成片
- 无拼图：按脚本短镜出分镜静帧拼图（可先核实），再图生视频
- 旁白/对白：edge-tts（线程内跑，避免事件循环冲突），整片一条音轨
- BGM：整片连续氛围底噪，不再用每镜环境音硬切
- 镜头间短交叉淡化，减轻卡顿/杂音感
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import folder_paths
import nodes
import numpy as np
import torch
import comfy.model_management
from comfy_api.latest import ComfyExtension, io
from comfy_api.latest._input_impl import VideoFromFile

from comfy_extras.nodes_minimax_h3 import MiniMaxH3ImageToVideo, align_frame_count, FPS
from comfy_extras.nodes_custom_sampler import (
    RandomNoise,
    BasicGuider,
    BasicScheduler,
    KSamplerSelect,
    SamplerCustomAdvanced,
)
from comfy_extras.nodes_video import CreateVideo

import math
import os

CANVAS_MULTIPLE = 32


def _video_canvas_limits() -> tuple[int, int]:
    short = int(os.environ.get("VIDEO_CANVAS_SHORT_EDGE", "352"))
    short = max(352, min(768, short))
    max_px = short * round(1344 * short / 768)
    return short, max_px


def _storyboard_for_output(storyboard_img, tiles: list):
    """Avoid `tensor or tensor` — bool(tensor) raises on multi-element tensors."""
    if storyboard_img is not None:
        return storyboard_img
    return tiles[0] if tiles else None


def _adapt_canvas_minimax(width: int, height: int) -> tuple[int, int]:
    """MiniMax H3 canvas; short edge from VIDEO_CANVAS_SHORT_EDGE (default 576)."""
    base_short, max_pixels = _video_canvas_limits()
    ratio = width / height
    if ratio >= 1.0:
        nom_w, nom_h = base_short * ratio, float(base_short)
    else:
        nom_w, nom_h = float(base_short), base_short / ratio
    if nom_w * nom_h > max_pixels:
        s = math.sqrt(max_pixels / (nom_w * nom_h))
        nom_w, nom_h = nom_w * s, nom_h * s
    w = max(CANVAS_MULTIPLE, round(nom_w / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
    h = max(CANVAS_MULTIPLE, round(nom_h / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
    return int(w), int(h)


def _is_black_shot_text(text: str) -> bool:
    """仅识别「本镜为黑场」意图，排除「禁止黑场/无切黑」等否定描述。"""
    t = (text or "").strip()
    if not t:
        return False
    if any(
        x in t
        for x in (
            "无切黑",
            "不切黑",
            "无黑场",
            "不要黑场",
            "非黑场",
            "无纯黑",
            "禁止黑场",
            "禁止切黑",
            "禁止旋转与黑场",
        )
    ):
        return False
    if re.search(r"(?:^|[，。；\n])(?:纯黑|全黑画面|切黑|fade to black)", t, re.I):
        return True
    if "黑场" in t and not re.search(r"[无不非禁止].{0,3}黑场", t):
        return True
    return False


def _drop_black_shots_for_video(
    tiles: list,
    scripts: list,
    voice_per_shot: list,
    durations: Optional[list] = None,
    ambience: Optional[list] = None,
    blocks: Optional[list] = None,
) -> tuple[list, list, list, list, list, list]:
    """成片不生成黑场镜；保留原镜号 orig_shot_nums（与 shot_XX.png 一致）。"""
    durs = durations if durations is not None else [6.0] * len(tiles)
    amb = ambience if ambience is not None else [""] * len(tiles)
    blks = blocks if blocks is not None else scripts
    out_t, out_s, out_v, out_d, out_orig, out_a = [], [], [], [], [], []
    for i, (tile, script, voice) in enumerate(zip(tiles, scripts, voice_per_shot)):
        blk = blks[i] if i < len(blks) else script
        if _is_black_shot_text(blk) or _is_black_shot_text(script):
            print(f"[Drama10] skip black shot {i + 1}/{len(tiles)} for video", flush=True)
            continue
        out_t.append(tile)
        out_s.append(script)
        out_v.append(voice)
        out_d.append(float(durs[i]) if i < len(durs) else 6.0)
        sn = _shot_num_from_block(blk)
        if sn <= 0:
            sn = i + 1
        out_orig.append(int(sn))
        out_a.append(amb[i] if i < len(amb) else "")
    if not out_t:
        return (
            tiles,
            scripts,
            voice_per_shot,
            durs,
            list(range(1, len(tiles) + 1)),
            amb,
        )
    return out_t, out_s, out_v, out_d, out_orig, out_a


def _parse_shot_durations(blocks: list[str], default_sec: float) -> list[float]:
    out: list[float] = []
    for block in blocks:
        m = re.search(
            r"^\s*\d{2}\s*[｜|][^｜|\n]*[｜|]\s*(\d+(?:\.\d+)?)\s*秒",
            (block or "").strip(),
            re.M,
        )
        if not m:
            m = re.search(r"(\d+(?:\.\d+)?)\s*秒", (block or "")[:80])
        out.append(float(m.group(1)) if m else float(default_sec))
    return out


NO_GRID_SUFFIX = (
    "\n单镜头全屏电影画面，只拍这一镜。"
    "严禁出现分镜拼图、九宫格、多画面同框、故事板、网格线、缩略图墙。"
)

# 角色声线：旁白浑厚、主角年轻有力、群臣多人不共用一条声
VOICE_MAP = {
    "旁白": ("zh-CN-YunjianNeural", "-6%"),
    "凌川": ("zh-CN-YunxiNeural", "+2%"),
    "神秘黑影": ("zh-CN-YunjianNeural", "-18%"),
    "黑影": ("zh-CN-YunjianNeural", "-18%"),
    "卡索": ("zh-CN-YunxiNeural", "+12%"),
    "樱空释": ("zh-CN-YunxiNeural", "+8%"),
    "侍卫": ("zh-CN-YunyangNeural", "-5%"),
    "大臣": ("zh-CN-YunjianNeural", "+6%"),
    "大臣们": ("__crowd__", "+0%"),
    "皇北城": ("zh-CN-YunjianNeural", "+4%"),
    "神秘声音": ("zh-CN-YunjianNeural", "-8%"),
    "默认": ("zh-CN-XiaoxiaoNeural", "+2%"),
}

CROWD_VOICE_LINES = [
    ("zh-CN-YunjianNeural", "+14%"),
    ("zh-CN-YunxiaNeural", "+16%"),
    ("zh-CN-YunyangNeural", "+10%"),
]

I2V_NO_TEXT_SUFFIX = (
    "严禁画面内出现字幕、汉字、字母、标题、水印、Logo、UI、横幅字；"
    "对白时刻也不要在画面显示台词文字。"
)

SR = 44100
XFADE_SEC = 0.52


def _looks_like_collage(image, cols: int = 5, rows: int = 2) -> bool:
    """过滤 Comfy 自带 example.png 等单图，避免误当成十镜拼图。"""
    if image is None:
        return False
    img = image[:1]
    _, h, w, _ = img.shape
    if h < 64 or w < 64:
        return False
    aspect = w / float(h)
    # 2×5 的 9:16 格子拼图整体偏横（约 45:32≈1.4）；竖图 9:16≈0.56 也可能是「整图嵌套拼图」
    # 典型默认 example 是 1:1（如 768×768）→ 直接判无效
    if 0.85 <= aspect <= 1.15:
        print(f"[Drama10] reject near-square image as collage {w}x{h}", flush=True)
        return False
    # 格子过小也不靠谱
    if (w // cols) < 64 or (h // rows) < 64:
        print(f"[Drama10] reject tiny cells {w}x{h} / {cols}x{rows}", flush=True)
        return False
    return True


def _save_storyboard_png(img: torch.Tensor, rel_prefix: str = "drama_ui/auto_storyboard") -> str:
    """立刻把分镜拼图写到 output，方便核实。"""
    import folder_paths
    from PIL import Image as PILImage

    t = img[:1].detach().cpu().float().clamp(0, 1)[0].numpy()
    if t.shape[-1] > 3:
        t = t[..., :3]
    arr = (t * 255.0).astype(np.uint8)
    out_dir = Path(folder_paths.get_output_directory())
    dest_dir = out_dir / Path(rel_prefix).parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{Path(rel_prefix).name}_{int(time.time())}.png"
    path = dest_dir / name
    PILImage.fromarray(arr).save(path)
    print(f"[Drama10] storyboard saved: {path}", flush=True)
    return str(path)


def _job_key(prompt: str, seed: int, width: int, height: int, length: int, steps: int, n_shots: int) -> str:
    raw = f"{prompt.strip()[:2000]}|{seed}|{width}x{height}|L{length}|S{steps}|N{n_shots}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _ckpt_dir(job_key: str) -> Path:
    d = Path(folder_paths.get_output_directory()) / "drama_ui" / "ckpt" / job_key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ckpt_source_sig(
    storyboard_path: Path, prompt: str, stills_md5: str = ""
) -> dict:
    collage_md5 = ""
    if storyboard_path.is_file():
        collage_md5 = hashlib.md5(storyboard_path.read_bytes()).hexdigest()[:16]
    prompt_md5 = hashlib.md5((prompt or "").strip().encode("utf-8")).hexdigest()[:16]
    sig: dict = {"collage_md5": collage_md5, "prompt_md5": prompt_md5}
    if stills_md5:
        sig["stills_md5"] = stills_md5
    return sig


def _purge_ckpt_i2v_cache(ckpt: Path) -> None:
    for pt in ckpt.glob("shot_*.pt"):
        try:
            pt.unlink()
        except OSError:
            pass
    mp4_dir = ckpt / "mp4"
    if mp4_dir.is_dir():
        for mp4 in mp4_dir.glob("*.mp4"):
            try:
                mp4.unlink()
            except OSError:
                pass
    for p in ckpt.glob("_xfade_step_*.mp4"):
        try:
            p.unlink()
        except OSError:
            pass
    for name in ("video_silent.mp4", "audio.wav", "DONE"):
        p = ckpt / name
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass


def _sync_ckpt_source_sig(
    ckpt: Path, storyboard_path: Path, prompt: str, stills_md5: str = ""
) -> None:
    """拼图/单镜图/脚本变更时丢弃旧 i2v，避免「新分镜 + 旧视频缓存」."""
    sig = _ckpt_source_sig(storyboard_path, prompt, stills_md5=stills_md5)
    sig_path = ckpt / "source_sig.json"
    old: dict = {}
    if sig_path.is_file():
        try:
            old = json.loads(sig_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            old = {}
    if old == sig:
        return
    if not old and (
        any(ckpt.glob("shot_*.pt"))
        or any((ckpt / "mp4").glob("*.mp4") if (ckpt / "mp4").is_dir() else [])
    ):
        print("[Drama10] legacy i2v cache (no source_sig) → purge", flush=True)
        _purge_ckpt_i2v_cache(ckpt)
        ap = ckpt / "audio.pt"
        if ap.is_file():
            try:
                ap.unlink()
            except OSError:
                pass
    if old.get("collage_md5") and old.get("collage_md5") != sig.get("collage_md5"):
        print("[Drama10] collage changed → purge i2v/mp4 cache", flush=True)
        _purge_ckpt_i2v_cache(ckpt)
    if old.get("stills_md5") and old.get("stills_md5") != sig.get("stills_md5"):
        print("[Drama10] per-shot stills changed → purge i2v/mp4 cache", flush=True)
        _purge_ckpt_i2v_cache(ckpt)
    elif sig.get("stills_md5") and not old.get("stills_md5"):
        print("[Drama10] switched to per-shot stills → purge legacy i2v cache", flush=True)
        _purge_ckpt_i2v_cache(ckpt)
    if old.get("prompt_md5") and old.get("prompt_md5") != sig.get("prompt_md5"):
        print("[Drama10] prompt/voices changed → purge audio cache", flush=True)
        ap = ckpt / "audio.pt"
        if ap.is_file():
            try:
                ap.unlink()
            except OSError:
                pass
    elif not old:
        print("[Drama10] fresh ckpt source sig", flush=True)
    sig_path.write_text(json.dumps(sig, ensure_ascii=False), encoding="utf-8")


def _import_tmp_shot_cache(ckpt: Path) -> int:
    """把 /tmp 里未完成的 shot_XX.pt 迁入持久目录，返回已有镜头数。"""
    imported = 0
    tmp_roots = sorted(Path("/tmp").glob("drama10_shots_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for root in tmp_roots[:5]:
        shots = sorted(root.glob("shot_*.pt"))
        if not shots:
            continue
        for sp in shots:
            dest = ckpt / sp.name
            if not dest.exists() or dest.stat().st_size < sp.stat().st_size:
                shutil.copy2(sp, dest)
                imported += 1
                print(f"[Drama10] import cache {sp} -> {dest}", flush=True)
        if imported:
            break
    # 最近自动分镜图
    sb_dst = ckpt / "storyboard.png"
    if not sb_dst.exists():
        out = Path(folder_paths.get_output_directory()) / "drama_ui"
        cands = sorted(out.glob("auto_storyboard_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands:
            shutil.copy2(cands[0], sb_dst)
            print(f"[Drama10] import storyboard {cands[0]} -> {sb_dst}", flush=True)
    return imported


def _shot_pt_path(ckpt: Path, shot_num: int) -> Path:
    """shot_num 为分镜表镜号（1-based），与 shot_XX.png / shot_XX.mp4 一致。"""
    return ckpt / f"shot_{int(shot_num):02d}.pt"


def _save_shot_pt(path: Path, imgs: torch.Tensor):
    # float16 省磁盘/内存
    torch.save(_as_thwc(imgs).detach().cpu().half().contiguous(), path)


def _load_shot_pt(path: Path) -> torch.Tensor:
    t = torch.load(path, map_location="cpu")
    return _as_thwc(t).float().contiguous()


def _release_vram_after_sampling() -> None:
    """采样结束后卸扩散/T5，给 Video VAE 解码留出 3090 显存."""
    try:
        comfy.model_management.unload_all_models()
    except Exception:
        pass
    gc.collect()
    comfy.model_management.soft_empty_cache()


def _encode_shot_mp4(pt_path: Path, mp4_path: Path, fps: float):
    """逐帧 pipe 到 ffmpeg，避免一次性 float32/uint8 全量驻留内存."""
    imgs = torch.load(pt_path, map_location="cpu", weights_only=False)
    imgs = _as_thwc(imgs)
    t, h, w, c = imgs.shape
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{w}x{h}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        str(mp4_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for i in range(t):
            frame = imgs[i]
            if frame.dtype != torch.uint8:
                frame = (frame.float().clamp(0, 1) * 255.0).round().to(torch.uint8)
            if frame.shape[-1] > 3:
                frame = frame[..., :3]
            proc.stdin.write(frame.contiguous().numpy().tobytes())
    finally:
        proc.stdin.close()
        del imgs
        gc.collect()
    rc = proc.wait()
    sz = mp4_path.stat().st_size if mp4_path.is_file() else 0
    if rc != 0 or sz < 512:
        raise RuntimeError(
            f"ffmpeg encode failed {pt_path} rc={rc} mp4={mp4_path.name} size={sz}"
        )
    print(f"[Drama10] encoded {mp4_path.name} frames={t}", flush=True)


def _shot_mp4_path(ckpt: Path, shot_num: int) -> Path:
    d = ckpt / "mp4"
    d.mkdir(exist_ok=True)
    return d / f"shot_{int(shot_num):02d}.mp4"


def _try_recover_shot_mp4(ckpt: Path, shot_num: int, fps: float) -> bool:
    pt = _shot_pt_path(ckpt, shot_num)
    mp4 = _shot_mp4_path(ckpt, shot_num)
    if not pt.is_file() or pt.stat().st_size < 1024 * 1024:
        return False
    if mp4.is_file() and mp4.stat().st_size > 8192:
        return True
    print(f"[Drama10] recover mp4 from {pt.name} (skip re-i2v)", flush=True)
    _release_vram_after_sampling()
    _encode_shot_mp4(pt, mp4, fps)
    return mp4.is_file()


def _probe_duration_sec(mp4: Path) -> float:
    probe = subprocess.check_output(
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
    ).strip()
    return float(probe or 0.0)


def _probe_wh(mp4: Path) -> tuple[int, int]:
    line = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(mp4),
        ],
        text=True,
    ).strip()
    w, h = line.split("x")
    return int(w), int(h)


def _ffmpeg_concat_copy(mp4s: list[Path], out_path: Path):
    lst = out_path.parent / "concat_list.txt"
    lines = []
    for p in mp4s:
        lines.append(f"file '{p.resolve().as_posix()}'")
    lst.write_text("\n".join(lines), encoding="utf-8")
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lst),
        "-c",
        "copy",
        str(out_path),
    ]
    subprocess.check_call(cmd)
    print(f"[Drama10] concat video (copy) -> {out_path}", flush=True)


def _ffmpeg_concat_xfade(mp4s: list[Path], out_path: Path, xfade_sec: float = XFADE_SEC):
    import shutil

    valid = [p for p in mp4s if p.is_file() and p.stat().st_size > 512]
    if not valid:
        raise RuntimeError("无有效镜头 mp4 可拼接")
    if len(valid) == 1:
        shutil.copyfile(valid[0], out_path)
        print(f"[Drama10] concat video (single) -> {out_path}", flush=True)
        return
    try:
        cur = out_path.parent / "_xfade_step_0.mp4"
        shutil.copyfile(valid[0], cur)
        xf = max(0.12, min(float(xfade_sec), 1.2))
        for i in range(1, len(valid)):
            nxt = valid[i]
            w, h = _probe_wh(cur)
            d_cur = _probe_duration_sec(cur)
            xf_i = min(xf, max(0.08, d_cur * 0.35))
            offset = max(0.05, d_cur - xf_i)
            step_out = out_path.parent / f"_xfade_step_{i}.mp4"
            scale = (
                f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24[v1s];"
            )
            fc = (
                f"[0:v]setpts=PTS-STARTPTS,fps=24[v0];{scale}"
                f"[v0][v1s]xfade=transition=fade:duration={xf_i:.3f}:offset={offset:.3f}[v]"
            )
            cmd = [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(cur),
                "-i",
                str(nxt),
                "-filter_complex",
                fc,
                "-map",
                "[v]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(step_out),
            ]
            subprocess.check_call(cmd)
            if i > 1 and cur.name.startswith("_xfade_step_"):
                try:
                    cur.unlink()
                except OSError:
                    pass
            cur = step_out
        shutil.copyfile(cur, out_path)
        print(f"[Drama10] concat video xfade({xf:.2f}s) -> {out_path}", flush=True)
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        print(f"[Drama10] xfade failed ({e}), fallback concat copy", flush=True)
        for p in out_path.parent.glob("_xfade_step_*.mp4"):
            try:
                p.unlink()
            except OSError:
                pass
        _ffmpeg_concat_copy(valid, out_path)


def _write_wav(audio: dict, path: Path):
    import wave

    w = audio["waveform"]
    if w.dim() == 3:
        w = w[0]
    # [C,T] -> interleaved int16
    w = w.detach().cpu().float().clamp(-1, 1)
    sr = int(audio["sample_rate"])
    pcm = (w.transpose(0, 1).contiguous().numpy() * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(pcm.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _wrap_subtitle_text(text: str, max_chars: int = 14, max_lines: int = 2) -> str:
    text = (text or "").strip().replace("\n", "")
    if len(text) <= max_chars:
        return text
    parts: list[str] = []
    i = 0
    while i < len(text) and len(parts) < max_lines:
        parts.append(text[i : i + max_chars])
        i += max_chars
    if i < len(text) and parts:
        parts[-1] = parts[-1][:-1] + "…"
    return "\n".join(parts)


def _write_srt(events: list[tuple[float, float, str]], path: Path) -> None:
    def _ts(sec: float) -> str:
        sec = max(0.0, sec)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines: list[str] = []
    for idx, (t0, t1, text) in enumerate(events, 1):
        text = _wrap_subtitle_text(text)
        if not text:
            continue
        if t1 <= t0:
            t1 = t0 + 1.2
        lines.append(str(idx))
        lines.append(f"{_ts(t0)} --> {_ts(t1)}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _probe_video_height(mp4: Path) -> int:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=height",
                "-of",
                "csv=p=0",
                str(mp4),
            ],
            text=True,
        ).strip()
        return max(480, int(float(out or 832)))
    except Exception:
        return 832


def _coerce_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _mp4_has_audio_stream(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 512:
        return False
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


def _ffmpeg_master_wav(wav_path: Path) -> None:
    """成片限幅，保留环境声低频（勿对整轨 afftdn）。"""
    if not wav_path.is_file():
        return
    tmp = wav_path.with_suffix(".master.wav")
    try:
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(wav_path),
                "-af",
                "highpass=f=60,alimiter=limit=0.95",
                str(tmp),
            ],
            timeout=120,
        )
        if tmp.is_file() and tmp.stat().st_size > 256:
            tmp.replace(wav_path)
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"[Drama10] master wav skip: {e}", flush=True)
        tmp.unlink(missing_ok=True)


def _ffmpeg_polish_speech_wav(wav_path: Path) -> None:
    """仅人声降噪；环境轨勿走此函数。"""
    if not wav_path.is_file():
        return
    tmp = wav_path.with_suffix(".polish.wav")
    af = (os.environ.get("DRAMA10_SPEECH_AF") or "highpass=f=110,lowpass=12000,afftdn=nf=-32").strip()
    try:
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(wav_path),
                "-af",
                af,
                str(tmp),
            ],
            timeout=120,
        )
        if tmp.is_file() and tmp.stat().st_size > 256:
            tmp.replace(wav_path)
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.SubprocessError, OSError) as e:
        print(f"[Drama10] polish wav skip: {e}", flush=True)
        tmp.unlink(missing_ok=True)


def _mux_av(
    video_mp4: Path,
    audio_wav: Path,
    out_mp4: Path,
    srt_path: Optional[Path] = None,
):
    if srt_path and srt_path.is_file() and srt_path.stat().st_size > 8:
        sp = str(srt_path).replace("\\", "/").replace(":", "\\:")
        vh = _probe_video_height(video_mp4)
        fs = max(13, min(16, int(vh * 0.017)))
        mv = max(48, int(vh * 0.065))
        style = (
            f"FontName=Noto Sans CJK SC,FontSize={fs},PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BackColour=&H60000000,Bold=0,"
            f"Outline=2,Shadow=1,MarginV={mv},Alignment=2,BorderStyle=1"
        )
        vf = f"subtitles='{sp}':force_style='{style}'"
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video_mp4),
            "-i",
            str(audio_wav),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(out_mp4),
        ]
    else:
        vonly = out_mp4.with_suffix(".vonly.mp4")
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video_mp4),
                "-an",
                "-c:v",
                "copy",
                str(vonly),
            ],
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(vonly),
            "-i",
            str(audio_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(out_mp4),
        ]
        subprocess.check_call(cmd)
        vonly.unlink(missing_ok=True)
    print(f"[Drama10] muxed -> {out_mp4}", flush=True)


def _load_png_tensor(path: Path, *, portrait: bool = False) -> torch.Tensor:
    from PIL import Image as PILImage, ImageOps

    im = ImageOps.exif_transpose(PILImage.open(path)).convert("RGB")
    w, h = im.size
    if portrait and w > h * 1.05:
        im = im.rotate(-90, expand=True)
    arr = np.array(im).astype(np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


def _downsample_gray_hw(frame: torch.Tensor, gh: int, gw: int) -> torch.Tensor:
    import torch.nn.functional as F

    x = frame.float().mean(-1).unsqueeze(0).unsqueeze(0)
    x = F.interpolate(x, size=(int(gh), int(gw)), mode="bilinear", align_corners=False)
    return x.squeeze()


def _best_rot90_k_vs_ref(ref: torch.Tensor, frame: torch.Tensor) -> int:
    """若 i2v 相对首帧参考转了 90°/270°，返回 rot90 次数 k∈{0,1,2,3}。"""
    ref = ref.detach().cpu().float()
    frame = frame.detach().cpu().float()
    gh = max(32, min(ref.shape[0], frame.shape[0]) // 6)
    gw = max(32, min(ref.shape[1], frame.shape[1]) // 6)
    ref_g = _downsample_gray_hw(ref, gh, gw)
    fr0 = _downsample_gray_hw(frame, gh, gw)
    best_k, best_mse, mse0 = 0, float("inf"), None
    import torch.nn.functional as F

    for k in range(4):
        fr = torch.rot90(fr0, k, [0, 1])
        if fr.shape != ref_g.shape:
            fr = F.interpolate(
                fr.unsqueeze(0).unsqueeze(0),
                size=ref_g.shape,
                mode="bilinear",
                align_corners=False,
            ).squeeze()
        mse = float(((fr - ref_g) ** 2).mean().item())
        if k == 0:
            mse0 = mse
        if mse < best_mse:
            best_mse, best_k = mse, k
    if best_k and mse0 is not None and best_mse < mse0 * 0.88:
        return int(best_k)
    return 0


def _resize_frames_batch(imgs: torch.Tensor, width: int, height: int) -> torch.Tensor:
    imgs = _as_thwc(imgs).detach().cpu().float()
    parts = [_resize_tile(imgs[i : i + 1], int(width), int(height)) for i in range(imgs.shape[0])]
    return torch.cat(parts, dim=0).contiguous()


def _correct_i2v_orientation(
    ref_tile: torch.Tensor,
    imgs: torch.Tensor,
    width: int,
    height: int,
    orig_sn: int = 0,
) -> torch.Tensor:
    """用输入首帧对齐 i2v 输出，修正模型偶发 90° 翻转（镜 7 等过肩镜头高发）。"""
    imgs = _as_thwc(imgs).detach().cpu().float().contiguous()
    ref = _as_thwc(ref_tile)[0]
    forced = int(os.environ.get(f"DRAMA10_FORCE_ROT90_SHOT_{int(orig_sn)}", "0") or 0)
    if forced in (1, 2, 3):
        k = forced
        print(f"[Drama10] upright fix shot{orig_sn}: forced rot90 k={k}", flush=True)
    else:
        k = _best_rot90_k_vs_ref(ref, imgs[0])
        if k == 0:
            return imgs
        print(f"[Drama10] upright fix shot{orig_sn}: rot90 k={k}", flush=True)
    fixed = torch.stack([torch.rot90(imgs[i], k, [0, 1]) for i in range(imgs.shape[0])])
    return _resize_frames_batch(fixed, int(width), int(height))


def _shot_stills_input_dir(subdir: str) -> Path:
    sub = (subdir or "").strip().replace("\\", "/").strip("/")
    return Path(folder_paths.get_input_directory()) / sub


def _shot_stills_md5(subdir: str, n_shots: int) -> str:
    base = _shot_stills_input_dir(subdir)
    h = hashlib.md5()
    found = 0
    for i in range(1, n_shots + 1):
        p = base / f"shot_{i:02d}.png"
        if p.is_file():
            h.update(p.read_bytes())
            found += 1
    return h.hexdigest()[:16] if found else ""


def _load_one_shot_still(
    subdir: str, shot_num: int, width: int, height: int
) -> torch.Tensor:
    base = _shot_stills_input_dir(subdir)
    sn = int(shot_num)
    p = base / f"shot_{sn:02d}.png"
    if not p.is_file():
        raise FileNotFoundError(f"缺少镜 {sn} 分镜图：{p}")
    portrait = int(height) > int(width)
    t = _load_png_tensor(p, portrait=portrait)
    return _resize_tile(t, int(width), int(height))


def _load_tiles_from_shot_stills(
    subdir: str,
    n_shots: int,
    width: int,
    height: int,
    *,
    shot_nums: Optional[list] = None,
) -> list:
    """按镜号加载 PNG（与脚本块 01｜02｜ 一致，避免第 2 镜误用 shot_01.png）。"""
    if shot_nums and len(shot_nums) == n_shots:
        nums = [int(x) for x in shot_nums]
    else:
        nums = list(range(1, int(n_shots) + 1))
    tiles = []
    for sn in nums:
        tiles.append(_load_one_shot_still(subdir, sn, width, height))
    return tiles


def _uw(out: Any):
    if out is None:
        return None
    if hasattr(out, "result") and out.result is not None:
        return out.result
    return out


def _run_coro(coro):
    """在独立线程里 asyncio.run，避免 ComfyUI 已有事件循环。"""
    def _runner():
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_runner).result(timeout=180)


def _split_grid(image, cols: int, rows: int, inset_ratio: float = 0.025):
    img = image[:1]
    _, h, w, _ = img.shape
    ch, cw = h // rows, w // cols
    iy = max(1, int(ch * inset_ratio))
    ix = max(1, int(cw * inset_ratio))
    tiles = []
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r * ch + iy, (r + 1) * ch - iy
            x0, x1 = c * cw + ix, (c + 1) * cw - ix
            if y1 <= y0 or x1 <= x0:
                y0, y1, x0, x1 = r * ch, (r + 1) * ch, c * cw, (c + 1) * cw
            tiles.append(img[:, y0:y1, x0:x1, :].contiguous())
    return tiles


def _make_collage(tiles: list, cols: int, rows: int) -> torch.Tensor:
    """tiles: list of [1,H,W,C] -> one collage [1,H*rows,W*cols,C]"""
    assert len(tiles) >= cols * rows
    # resize all to same H,W (min)
    hs = [t.shape[1] for t in tiles[: cols * rows]]
    ws = [t.shape[2] for t in tiles[: cols * rows]]
    th, tw = min(hs), min(ws)
    norm = []
    for t in tiles[: cols * rows]:
        if t.shape[1] != th or t.shape[2] != tw:
            # nearest via narrow slice / pad
            t2 = t[:, :th, :tw, :]
            if t2.shape[1] < th or t2.shape[2] < tw:
                pad = torch.zeros(1, th, tw, t.shape[-1], device=t.device, dtype=t.dtype)
                pad[:, : t2.shape[1], : t2.shape[2], :] = t2
                t2 = pad
            norm.append(t2)
        else:
            norm.append(t)
    rows_img = []
    for r in range(rows):
        rows_img.append(torch.cat(norm[r * cols : (r + 1) * cols], dim=2))
    return torch.cat(rows_img, dim=1)


def _shot_num_from_block(block: str) -> int:
    m = re.match(r"^\s*(\d{1,2})\s*[｜|]", (block or "").strip())
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def _block_for_shot_num(prompt: str, shot_num: int) -> str:
    text = (prompt or "").strip()
    sn = int(shot_num)
    heads = list(re.finditer(r"(?:^|\n)\s*(\d{1,2})\s*[｜|]\s*[^\n]*", text))
    for i, m in enumerate(heads):
        try:
            if int(m.group(1)) == sn:
                start = m.start()
                end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
                return text[start:end].strip()
        except ValueError:
            continue
    raise ValueError(f"Prompt 中找不到镜号 {sn}（共 {len(heads)} 个镜头块）")


def _count_shot_markers(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    return len(re.findall(r"(?:^|\n)\s*0?\d{1,2}\s*[｜|]\s*[^\n]*", text))


def _grid_for_shots(n: int, cols: int, rows: int) -> tuple[int, int]:
    cols, rows = max(1, int(cols)), max(1, int(rows))
    if cols * rows == n:
        return cols, rows
    if n == 10:
        return 5, 2
    if n <= 0:
        return cols, rows
    best = (n, 1)
    for r in range(1, n + 1):
        if n % r == 0:
            c = n // r
            if abs(c - 2 * r) < abs(best[0] - 2 * best[1]):
                best = (c, r)
    return best


def _shot_blocks(text: str, n: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return [""] * n
    heads = list(re.finditer(r"(?:^|\n)\s*0?\d{1,2}\s*[｜|]\s*[^\n]*", text))
    if len(heads) >= min(n, 2):
        blocks = []
        for i, m in enumerate(heads[:n]):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            blocks.append(text[m.start() : end])
        while len(blocks) < n:
            blocks.append(blocks[-1] if blocks else "")
        return blocks[:n]
    return [text] * n


def _parse_scripts(text: str, n: int) -> list[str]:
    out = []
    for block in _shot_blocks(text, n):
        vm = re.search(
            r"视频提示词\s*[：:]\s*(.*?)(?:\n\s*(?:旁白|对白|声音|神秘声音)\s*[：:]|\Z)",
            block,
            re.S,
        )
        prompt = (vm.group(1).strip() if vm else block.strip()) + NO_GRID_SUFFIX
        out.append(prompt)
    while len(out) < n:
        out.append(out[-1] if out else NO_GRID_SUFFIX)
    return out[:n]


def _clean_quote(s: str) -> str:
    s = re.sub(r"^[\s「\"“]+|[\s」\"”]+$", "", (s or "").strip())
    s = re.sub(r"\s+", "", s)  # 中文TTS更清晰：去掉多余空白
    # 保留省略号语义
    s = s.replace("……", "…").replace("...", "…")
    return s


def _voice_start_sec(suffix: str, default: float = 0.5) -> float:
    m = re.search(r"[（(]([\d.]+)\s*秒", suffix or "")
    if not m:
        return default
    try:
        return max(0.0, float(m.group(1)))
    except ValueError:
        return default


def _voice_script_tail(block: str) -> str:
    """仅解析块末尾的旁白/对白/声音行，避免【人物表演】内台词被 TTS 重复读。"""
    if not block:
        return ""
    parts: list[str] = []
    for m in re.finditer(
        r"(?:^|\n)\s*((?:旁白|对白|声音|神秘声音)\s*[：:].*?)(?=(?:\n\s*(?:旁白|对白|声音|神秘声音)\s*[：:])|\Z)",
        block,
        re.S,
    ):
        line = m.group(1).strip()
        if line and line not in parts:
            parts.append(line)
    return "\n".join(parts)


def _parse_voice_lines(block: str) -> list[tuple[str, str, float]]:
    lines: list[tuple[str, str, float]] = []
    seen = set()

    def add(role: str, text: str, start_sec: float = 0.5):
        t = _clean_quote(text)
        if not t or len(t) < 2:
            return
        key = (role, t, round(start_sec, 2))
        if key in seen:
            return
        seen.add(key)
        lines.append((role, t, float(start_sec)))

    if not block:
        return lines

    tail = _voice_script_tail(block)
    parse_src = tail if tail.strip() else block

    # 旁白段
    for m in re.finditer(
        r"(?:^|\n)\s*旁白\s*[：:]\s*(.*?)(?=\n\s*(?:对白|声音|神秘声音|视频提示词|画面)\s*[：:]|\Z)",
        parse_src,
        re.S,
    ):
        chunk = m.group(1)
        for qm in re.finditer(r"[「\"“](.+?)[」\"”]\s*([^ \n]*)", chunk, re.S):
            add("旁白", qm.group(1), _voice_start_sec(qm.group(2)))
        if not re.search(r"[「\"“].+?[」\"”]", chunk):
            add("旁白", re.sub(r"[「」\"“”]", "", chunk))

    # 神秘声音：“...”
    for m in re.finditer(r"(?:^|\n)\s*神秘声音\s*[：:]\s*[「\"“](.+?)[」\"”]", parse_src, re.S):
        add("神秘声音", m.group(1))

    # 对白：角色「台词」（卡索/大臣/皇北城等）
    for m in re.finditer(
        r"(?:^|\n)\s*对白\s*[：:]\s*([^\n「\"“]+?)[「\"“](.+?)[」\"”]\s*([^ \n]*)",
        parse_src,
        re.S,
    ):
        role = re.sub(r"\s+", "", m.group(1).strip().rstrip("：:"))
        add(role or "默认", m.group(2), _voice_start_sec(m.group(3)))

    if tail.strip():
        return lines

    # 对白段内 凌川…“...”（旧格式兼容，仅无独立声音行时）
    for m in re.finditer(
        r"(?:^|\n)\s*对白\s*[：:]\s*(.*?)(?=\n\s*(?:旁白|声音|神秘声音|视频提示词|画面)\s*[：:]|\Z)",
        parse_src,
        re.S,
    ):
        chunk = m.group(1)
        if re.search(r"[「\"“].+?[」\"”]", chunk):
            continue
        for qm in re.finditer(r"凌川[^\n「\"“]{0,16}[「\"“](.+?)[」\"”]", chunk, re.S):
            add("凌川", qm.group(1))
        for qm in re.finditer(r"神秘声音[^\n「\"“]{0,8}[「\"“](.+?)[」\"”]", chunk, re.S):
            add("神秘声音", qm.group(1))

    block_wo_narr = re.sub(
        r"(?:^|\n)\s*旁白\s*[：:].*?(?=\n\s*(?:对白|声音|神秘声音|视频提示词|画面)\s*[：:]|\Z)",
        "\n",
        parse_src,
        flags=re.S,
    )
    for m in re.finditer(r"凌川[^\n「\"“]{0,16}[「\"“](.+?)[」\"”]", block_wo_narr, re.S):
        add("凌川", m.group(1))
    for m in re.finditer(r"神秘声音[^\n「\"“]{0,8}[「\"“](.+?)[」\"”]", block_wo_narr, re.S):
        add("神秘声音", m.group(1))

    return lines


def _parse_ambience_line(block: str) -> str:
    m = re.search(r"(?:^|\n)\s*声音\s*[：:]\s*(.+?)(?=\n\s*(?:旁白|对白|视频提示词|画面)\s*[：:]|\Z)", block, re.S)
    return (m.group(1).strip() if m else "")[:200]


_NARRATOR_ROLES = frozenset({"旁白", "OS", "os", "narrator", "Narrator"})


def _is_narrator_voice_role(role: str) -> bool:
    return (role or "").strip() in _NARRATOR_ROLES


def _narrator_lip_i2v_hint() -> str:
    return (
        "本镜为画外旁白：音轨可播旁白，但画面内所有人物嘴唇全程闭合静止，"
        "禁止对口型、禁止随旁白节奏张嘴；仅呼吸眨眼与环境动效，嘴部不动。"
    )


def _i2v_lip_sync_hint(block: str) -> str:
    lines = _parse_voice_lines(block)
    if not lines:
        return (
            "本镜无对白：人物口型基本静止，仅自然呼吸；不要说话式张合嘴。"
            "环境动效与嘴型无关。"
        )
    on_screen = [(r, t, st) for r, t, st in lines if not _is_narrator_voice_role(r)]
    narrators = [(r, t, st) for r, t, st in lines if _is_narrator_voice_role(r)]
    if not on_screen:
        return _narrator_lip_i2v_hint()
    if len(on_screen) == 1:
        role, text, st = on_screen[0]
        if role in ("大臣们",):
            return (
                f"镜内第{st:.1f}秒起，多名跪拜大臣齐声喊「{text[:16]}」，"
                "可表现为群体张口齐呼；非说话者闭口。"
                + (" 画外旁白不对口型。" if narrators else "")
            )
        return (
            f"镜内第{st:.1f}秒起仅「{role}」开口，口型随「{text[:24]}」自然开合；"
            f"第{st:.1f}秒之前全员闭口；其他人物静听口型不动。"
            + (" 画外旁白不对口型。" if narrators else "")
        )
    parts = []
    for role, text, st in on_screen:
        parts.append(f"第{st:.1f}秒「{role}」：{text[:12]}…")
    return (
        "画内对白按下列时间点依次开口（与成片音轨对齐），同一时刻仅一人口型在动："
        + "；".join(parts)
        + "。禁止多人同时对不同台词口型。"
        + (" 画外旁白不对口型。" if narrators else "")
    )


def _shot_ambience_noise(kind: str, n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    t = np.arange(n, dtype=np.float64) / SR
    alpha = 0.02
    noise = rng.randn(n).astype(np.float64)
    wind = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        wind[i] = wind[i - 1] + alpha * (noise[i] - wind[i - 1])
    if "风" in kind or "雪" in kind or "wind" in kind.lower() or "snow" in kind.lower():
        wind *= 0.48
        if "旗" in kind or "猎猎" in kind:
            wind *= 1.08
    elif "脚步" in kind or "铠甲" in kind:
        wind *= 0.12
        for _ in range(max(3, int(n / SR / 2))):
            pos = rng.randint(0, max(1, n - int(0.05 * SR)))
            wind[pos : pos + int(0.04 * SR)] += 0.25
    elif "低" in kind or "混响" in kind or "大厅" in kind:
        wind *= 0.08
    else:
        wind *= 0.15
    return wind.astype(np.float32)


def _build_ambience_timeline(
    ambience_per_shot: list[str], durations: list[float], seed: int = 42
) -> torch.Tensor:
    total_sec = sum(float(d) for d in durations) + 1.5
    n = max(1, int(total_sec * SR))
    track = np.zeros(n, dtype=np.float32)
    t0 = 0.0
    for i, (dur, desc) in enumerate(zip(durations, ambience_per_shot)):
        desc = (desc or "").strip()
        if desc:
            seg_n = int(float(dur) * SR)
            start = int(t0 * SR)
            end = min(n, start + seg_n)
            if end > start:
                mono = _shot_ambience_noise(desc, end - start, seed=seed + i)
                track[start:end] += mono
        t0 += float(dur)
    if track.max() > 1e-6:
        track = track / (track.max() + 1e-6) * 0.52
    stereo = np.stack([track, track * 0.97], axis=0)
    return torch.from_numpy(stereo).unsqueeze(0).float()


async def _edge_tts_file(
    text: str, voice: str, rate: str, path: Path, *, pitch: str = "+0Hz"
):
    import edge_tts

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    except TypeError:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(path))


def _load_audio_ffmpeg(path: Path, target_sr: int = SR) -> torch.Tensor:
    """ffmpeg 解码为 [1,2,T]，不依赖 torchcodec。"""
    import subprocess

    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "2",
        "-ar",
        str(target_sr),
        "pipe:1",
    ]
    raw = subprocess.check_output(cmd)
    audio = torch.frombuffer(bytearray(raw), dtype=torch.float32).reshape(-1, 2).transpose(0, 1).contiguous()
    return audio.unsqueeze(0)


def _studio_ohmygpt_creds() -> tuple[str, str]:
    base = (
        os.environ.get("STORYBOARD_IMAGE_API_BASE")
        or os.environ.get("OHMYGPT_API_BASE")
        or ""
    ).strip().rstrip("/")
    key = (
        os.environ.get("STORYBOARD_IMAGE_API_KEY")
        or os.environ.get("OHMYGPT_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    db_path = Path("/home/linux/bingjing/web/data/studio.db")
    if db_path.is_file():
        try:
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            try:
                if not key:
                    row = conn.execute(
                        "SELECT value FROM studio_settings WHERE key=?",
                        ("ohmygpt_api_key",),
                    ).fetchone()
                    key = (row[0] if row else "") or ""
                if not base:
                    row = conn.execute(
                        "SELECT value FROM studio_settings WHERE key=?",
                        ("ohmygpt_api_base",),
                    ).fetchone()
                    base = (row[0] if row else "") or "https://api.ohmygpt.com/v1"
            finally:
                conn.close()
        except Exception as e:
            print(f"[Drama10] studio.db creds: {e}", flush=True)
    if not base:
        base = "https://api.ohmygpt.com/v1"
    return base.rstrip("/"), key.strip()


def _http_api_voice_for_role(role: str) -> str:
    role = (role or "旁白").strip()
    raw = (os.environ.get("DRAMA10_ROLE_VOICES_JSON") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and role in data:
                v = str(data[role]).strip()
                if v:
                    return v
        except json.JSONDecodeError:
            pass
    if role in ("旁白", "OS", "os"):
        return (os.environ.get("DRAMA10_NARRATOR_VOICE") or "onyx").strip() or "onyx"
    if role in ("大臣们", "群臣"):
        return (os.environ.get("DRAMA10_CROWD_VOICE") or "onyx").strip() or "onyx"
    role_map = {
        "凌川": "echo",
        "神秘黑影": "onyx",
        "黑影": "onyx",
        "卡索": "echo",
        "樱空释": "fable",
        "皇北城": "fable",
        "侍卫": "ash",
        "大臣": "echo",
        "神秘声音": "onyx",
    }
    if role in role_map:
        return role_map[role]
    return (os.environ.get("CHAR_TTS_VOICE") or "echo").strip() or "echo"


def _http_tts_file_voice(text: str, path: Path, voice: str) -> bool:
    import httpx

    base, key = _studio_ohmygpt_creds()
    if not key:
        return False
    voice = (voice or "echo").strip() or "echo"
    model = (
        os.environ.get("CHAR_TTS_MODEL")
        or os.environ.get("OHMYGPT_TTS_MODEL")
        or "tts-1"
    ).strip()
    body = {
        "model": model,
        "input": (text or "").strip()[:800],
        "voice": voice,
        "response_format": "mp3",
    }
    proxy = (os.environ.get("STORYBOARD_IMAGE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY") or "").strip()
    try:
        kw: dict = {"timeout": 120.0}
        if proxy:
            kw["proxy"] = proxy
        with httpx.Client(**kw) as client:
            resp = client.post(
                f"{base}/audio/speech",
                json=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        if resp.status_code >= 400 or len(resp.content) < 128:
            return False
        path.write_bytes(resp.content)
        print(f"[Drama10] HTTP TTS ok voice={voice} bytes={len(resp.content)}", flush=True)
        return True
    except Exception as e:
        print(f"[Drama10] HTTP TTS fail voice={voice}: {e}", flush=True)
        return False


def _http_tts_file(text: str, path: Path, role: str) -> bool:
    import httpx

    base, key = _studio_ohmygpt_creds()
    if not key:
        return False
    voice = _http_api_voice_for_role(role)
    model = (
        os.environ.get("CHAR_TTS_MODEL")
        or os.environ.get("OHMYGPT_TTS_MODEL")
        or "tts-1"
    ).strip()
    body = {
        "model": model,
        "input": (text or "").strip()[:800],
        "voice": voice,
        "response_format": "mp3",
    }
    proxy = (os.environ.get("STORYBOARD_IMAGE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY") or "").strip()
    try:
        kw: dict = {"timeout": 120.0}
        if proxy:
            kw["proxy"] = proxy
        with httpx.Client(**kw) as client:
            resp = client.post(
                f"{base}/audio/speech",
                json=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        if resp.status_code >= 400 or len(resp.content) < 128:
            print(
                f"[Drama10] HTTP TTS bad status={resp.status_code} len={len(resp.content)}",
                flush=True,
            )
            return False
        path.write_bytes(resp.content)
        print(
            f"[Drama10] HTTP TTS ok role={role} voice={voice} bytes={len(resp.content)}",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[Drama10] HTTP TTS fail: {e}", flush=True)
        return False


def _tts_clip_single(
    text: str, voice: str, rate: str, pitch: str = "+0Hz", *, role: str = "默认"
) -> torch.Tensor:
    with tempfile.TemporaryDirectory(prefix="d10tts_") as td:
        outp = Path(td) / "a.mp3"
        if not _http_tts_file(text, outp, role):
            _run_coro(_edge_tts_file(text, voice, rate, outp, pitch=pitch))
        return _load_audio_ffmpeg(outp, SR)


def _apply_tts_fade(clip: torch.Tensor, ms: int = 12) -> torch.Tensor:
    n = clip.shape[-1]
    fade = min(n // 4, int(ms / 1000.0 * SR))
    if fade < 2:
        return clip
    ramp = torch.linspace(0, 1, fade, dtype=clip.dtype)
    clip = clip.clone()
    clip[:, :, :fade] *= ramp
    clip[:, :, -fade:] *= ramp.flip(0)
    return clip


def _tts_wav_tensor(text: str, role: str) -> torch.Tensor:
    """Return [1,2,T] float32."""
    voice, rate = VOICE_MAP.get(role, VOICE_MAP["默认"])
    pitch = "+2Hz" if role == "卡索" else "+0Hz"
    if role == "旁白":
        pitch = "-2Hz"
    if voice == "__crowd__":
        api_voices = [
            v.strip()
            for v in (
                os.environ.get("DRAMA10_CROWD_VOICES") or "echo,fable,ash,nova,onyx"
            ).split(",")
            if v.strip()
        ][:5]
        clips: list[torch.Tensor] = []
        for i, av in enumerate(api_voices):
            with tempfile.TemporaryDirectory(prefix="d10crowd_") as td:
                outp = Path(td) / "a.mp3"
                ok = _http_tts_file_voice(text, outp, av)
                if not ok and i < len(CROWD_VOICE_LINES):
                    cv, cr = CROWD_VOICE_LINES[i]
                    _run_coro(_edge_tts_file(text, cv, cr, outp, pitch="+0Hz"))
                c = _load_audio_ffmpeg(outp, SR)
            vol = (1.0, 0.88, 0.82, 0.76, 0.68)[i] if i < 5 else 0.65
            clips.append(c * vol)
        max_len = max(c.shape[-1] for c in clips) if clips else int(0.5 * SR)
        mix = torch.zeros(1, 2, max_len)
        for i, c in enumerate(clips):
            off = int((0, 0, 55, 110, 165)[i if i < 5 else 4] / 1000.0 * SR)
            end = min(max_len, off + c.shape[-1])
            if off >= max_len:
                continue
            seg = c[:, :, : end - off]
            mix[:, :, off:end] += seg
        peak = mix.abs().max().clamp(min=1e-6)
        clip = (mix / peak * 0.84).clamp(-1, 1)
    else:
        clip = _tts_clip_single(text, voice, rate, pitch=pitch, role=role)
        peak = clip.abs().max().clamp(min=1e-6)
        clip = (clip / peak * 0.78).clamp(-1, 1)
    return _apply_tts_fade(clip)


def _continuous_bgm(total_sec: float, seed: int = 42) -> torch.Tensor:
    """整片连续氛围底（粉噪+低通，无单频 hum）。[1,2,T]"""
    rng = np.random.RandomState(seed)
    n = max(1, int(total_sec * SR))
    noise = rng.randn(n).astype(np.float64)
    alpha = 0.022
    wind = np.zeros_like(noise)
    for i in range(1, n):
        wind[i] = wind[i - 1] + alpha * (noise[i] - wind[i - 1])
    wind_gain = float(os.environ.get("DRAMA10_BGM_WIND", "0.55"))
    mono = (wind_gain * wind).astype(np.float32)
    fade = int(2.0 * SR)
    if fade * 2 < n:
        mono[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        mono[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    peak = np.max(np.abs(mono)) + 1e-6
    bgm_peak = float(os.environ.get("DRAMA10_BGM_LEVEL", "0.42"))
    mono = mono / peak * bgm_peak
    stereo = np.stack([mono, mono * 0.96], axis=0)
    return torch.from_numpy(stereo).unsqueeze(0)


def _speech_sync_offset(shot_index: int, *, episode_wide: bool = True) -> float:
    lag = float(os.environ.get("DRAMA10_MOUTH_LAG", "1.0"))
    if not episode_wide:
        return lag
    drift = float(os.environ.get("DRAMA10_DRIFT_PER_SHOT", "0.15"))
    return lag + max(0, int(shot_index)) * drift


def _build_speech_timeline(
    voice_per_shot: list[list[tuple[str, str, float]]],
    shot_starts: list[float],
    shot_durations: list[float],
    *,
    episode_shot_indices: Optional[list[int]] = None,
    episode_wide_sync: bool = True,
) -> tuple[torch.Tensor, list[tuple[float, float, str]]]:
    """按成片镜起点 + 镜内 start_sec + 口型滞后补偿落位 TTS；返回波形与字幕事件。"""
    if not shot_starts:
        shot_starts = []
        acc = 0.0
        for d in shot_durations:
            shot_starts.append(acc)
            acc += float(d)
    tail = (shot_starts[-1] + float(shot_durations[-1])) if shot_durations else 0.0
    total_sec = tail + 1.5
    n = max(1, int(total_sec * SR))
    track = torch.zeros(1, 2, n)
    sub_events: list[tuple[float, float, str]] = []
    for i, lines in enumerate(voice_per_shot):
        base = float(shot_starts[i]) if i < len(shot_starts) else 0.0
        if not lines:
            print(f"[Drama10] TTS shot{i+1}: (silent)", flush=True)
        for role, text, start_in_shot in lines:
            text = (text or "").strip()
            if len(text) < 1:
                continue
            gi = (
                episode_shot_indices[i]
                if episode_shot_indices and i < len(episode_shot_indices)
                else i
            )
            sync_off = _speech_sync_offset(gi, episode_wide=episode_wide_sync)
            st = base + float(start_in_shot) + sync_off
            try:
                clip = _tts_wav_tensor(text, role)
                start = int(st * SR)
                end = min(n, start + clip.shape[-1])
                if start >= n:
                    continue
                seg = clip[:, :, : end - start]
                track[:, :, start:end] += seg
                dur = seg.shape[-1] / float(SR)
                sub_events.append((st, st + dur, text))
                print(
                    f"[Drama10] TTS ok t={st:.2f}s "
                    f"(shot+{start_in_shot:.1f}s+sync{sync_off:.2f}) {role}: {text[:40]}",
                    flush=True,
                )
            except Exception as e:
                print(f"[Drama10] TTS FAIL shot{i+1} {role}: {e}", flush=True)
    peak = track.abs().max().clamp(min=1e-6)
    return (track / peak * 0.75).clamp(-1, 1), sub_events


def _mux_one_shot_preview_audio(
    ckpt: Path,
    silent_mp4: Path,
    voice_lines: list,
    ambience: str,
    noise_seed: int,
) -> Path:
    """单镜预览：本镜 TTS + BGM + 环境声，写入同路径 mp4（覆盖 silent）。"""
    if not silent_mp4.is_file() or silent_mp4.stat().st_size < 512:
        raise RuntimeError(f"单镜混音：视频不存在或过小 {silent_mp4}")
    if not voice_lines:
        print("[Drama10] mux: no voice lines (BGM/环境声仍会混入)", flush=True)
    dur = _probe_duration_sec(silent_mp4)
    speech, _ = _build_speech_timeline(
        [voice_lines],
        [0.0],
        [dur],
        episode_wide_sync=False,
    )
    preview_bgm = float(os.environ.get("DRAMA10_PREVIEW_BGM_GAIN", "0.22"))
    bgm = _continuous_bgm(dur + 0.8, seed=int(noise_seed))
    sfx = _build_ambience_timeline([ambience or ""], [dur], seed=int(noise_seed) + 17)
    audio = _mix_audio(speech, bgm, sfx, bgm_mix_gain=preview_bgm)
    need = max(1, int(dur * SR))
    w = audio["waveform"]
    if w.dim() == 2:
        w = w.unsqueeze(0)
    if w.shape[-1] > need:
        w = w[:, :, :need]
    elif w.shape[-1] < need:
        w = torch.cat([w, torch.zeros(1, w.shape[1], need - w.shape[-1])], dim=-1)
    wav_path = ckpt / f"preview_{silent_mp4.stem}.wav"
    _write_wav({"waveform": w, "sample_rate": SR}, wav_path)
    _ffmpeg_master_wav(wav_path)
    out_mp4 = silent_mp4.with_suffix(".av.mp4")
    _mux_av(silent_mp4, wav_path, out_mp4, None)
    silent_mp4.write_bytes(out_mp4.read_bytes())
    out_mp4.unlink(missing_ok=True)
    if not _mp4_has_audio_stream(silent_mp4):
        raise RuntimeError(
            "单镜混音失败：mp4 无音轨（请检查 ffmpeg、edge-tts 与网络）"
        )
    print(f"[Drama10] single-shot preview with audio -> {silent_mp4.name}", flush=True)
    return silent_mp4


def _mix_audio(
    speech: torch.Tensor,
    bgm: torch.Tensor,
    sfx: Optional[torch.Tensor] = None,
    *,
    bgm_mix_gain: float = 1.0,
) -> dict:
    n = max(speech.shape[-1], bgm.shape[-1])
    if speech.shape[-1] < n:
        speech = torch.cat([speech, torch.zeros(1, 2, n - speech.shape[-1])], dim=-1)
    if bgm.shape[-1] < n:
        bgm = torch.cat([bgm, torch.zeros(1, 2, n - bgm.shape[-1])], dim=-1)
    if sfx is not None:
        if sfx.shape[-1] < n:
            sfx = torch.cat([sfx, torch.zeros(1, 2, n - sfx.shape[-1])], dim=-1)
        else:
            sfx = sfx[:, :, :n]
        sfx_gain = float(os.environ.get("DRAMA10_SFX_MIX_GAIN", "0.58"))
        sfx = sfx * sfx_gain
        bgm = (bgm * 0.88 + sfx).clamp(-1, 1)
    else:
        bgm = bgm * 0.88
    bgm = bgm * float(bgm_mix_gain)
    sp_env = speech.abs().mean(dim=1, keepdim=True)
    # 平滑
    win = int(0.05 * SR)
    if win > 1:
        pad = torch.nn.functional.pad(sp_env, (win // 2, win - win // 2), mode="reflect")
        sp_env = torch.nn.functional.avg_pool1d(pad, win, stride=1)
        sp_env = sp_env[:, :, :n]
    duck = (1.0 - 0.28 * (sp_env / (sp_env.max() + 1e-6)).clamp(0, 1)).clamp(0.62, 1.0)
    mixed = speech + bgm * duck
    mixed = torch.tanh(mixed * 1.15) * 0.92
    return {"waveform": mixed, "sample_rate": SR}


def _images_from_video(video) -> torch.Tensor:
    comps = video.get_components()
    return comps.images


def _as_thwc(im: torch.Tensor) -> torch.Tensor:
    if im.dim() == 5:
        im = im[0]
    if im.dim() != 4:
        raise ValueError(f"bad image tensor {tuple(im.shape)}")
    return im


def _resize_tile(tile: torch.Tensor, width: int, height: int) -> torch.Tensor:
    """拼图切格后尺寸常非 8 对齐，统一缩放到成片宽高再 I2V。"""
    import torch.nn.functional as F

    t = _as_thwc(tile).detach().cpu().float()
    if t.shape[1] == int(height) and t.shape[2] == int(width):
        return t.contiguous()
    t = t.permute(0, 3, 1, 2)
    t = F.interpolate(
        t, size=(int(height), int(width)), mode="bilinear", align_corners=False
    )
    return t.permute(0, 2, 3, 1).contiguous()


def _match_hw(ref: torch.Tensor, nxt: torch.Tensor) -> torch.Tensor:
    th, tw = ref.shape[1], ref.shape[2]
    if nxt.shape[1] == th and nxt.shape[2] == tw:
        return nxt
    nh, nw = nxt.shape[1], nxt.shape[2]
    out = torch.zeros(nxt.shape[0], th, tw, nxt.shape[-1], dtype=nxt.dtype)
    h, w = min(th, nh), min(tw, nw)
    out[:, :h, :w] = nxt[:, :h, :w]
    return out


def _crossfade_pair(left: torch.Tensor, right: torch.Tensor, fps: float, xfade_sec: float = XFADE_SEC) -> torch.Tensor:
    """两段 [T,H,W,C] 交叉淡化拼接，全程 CPU，控制峰值内存。"""
    left = _as_thwc(left).detach().cpu().float().contiguous()
    right = _match_hw(left, _as_thwc(right).detach().cpu().float().contiguous())
    xfade = max(1, int(round(xfade_sec * fps)))
    ov = min(xfade, left.shape[0] // 2, right.shape[0] // 2)
    if ov < 1:
        return torch.cat([left, right], dim=0)
    wts = torch.linspace(0, 1, ov).view(ov, 1, 1, 1)
    mid = left[-ov:] * (1.0 - wts) + right[:ov] * wts
    out = torch.cat([left[:-ov], mid, right[ov:]], dim=0)
    del left, right, mid, wts
    return out


def _gen_one_shot(
    model,
    clip,
    vae,
    prompt: str,
    width: int,
    height: int,
    length: int,
    first_frame,
    noise_seed: int,
    steps: int,
    sampler,
):
    pos, latent = _uw(
        MiniMaxH3ImageToVideo.execute(
            clip=clip,
            vae=vae,
            prompt=prompt,
            width=int(width),
            height=int(height),
            length=int(length),
            first_frame=first_frame,
            last_frame=None,
        )
    )
    noise = _uw(RandomNoise.execute(noise_seed=int(noise_seed)))[0]
    guider = _uw(BasicGuider.execute(model=model, conditioning=pos))[0]
    sigmas = _uw(BasicScheduler.execute(model=model, scheduler="simple", steps=int(steps), denoise=1.0))[0]
    out_lat, _ = _uw(
        SamplerCustomAdvanced.execute(
            noise=noise,
            guider=guider,
            sampler=sampler,
            sigmas=sigmas,
            latent_image=latent,
        )
    )
    del noise, guider, sigmas, pos, latent, sampler
    gc.collect()
    _release_vram_after_sampling()
    print("[Drama10] VAE decode after unload diffusion/TE…", flush=True)
    images = nodes.VAEDecode().decode(vae, out_lat)[0]
    del out_lat
    gc.collect()
    comfy.model_management.soft_empty_cache()
    return _as_thwc(images).detach().cpu().half().contiguous()


class Drama10StoryboardMiniMaxH3(io.ComfyNode):
    """无上传图时：按脚本用 H3 短镜出 10 张静帧并拼成 2×5，便于核实。"""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Drama10StoryboardMiniMaxH3",
            display_name="短剧10镜 分镜图生成 (MiniMax H3)",
            category="video/minimax",
            description="根据脚本自动生成 2×5 分镜拼图（短采样取首帧）。核实后再接到图生视频节点。",
            inputs=[
                io.Model.Input("model"),
                io.Clip.Input("clip"),
                io.Vae.Input("vae"),
                io.String.Input("prompt", multiline=True, default=""),
                io.Int.Input("width", default=352, min=32, max=nodes.MAX_RESOLUTION, step=32),
                io.Int.Input("height", default=608, min=32, max=nodes.MAX_RESOLUTION, step=32),
                io.Int.Input("noise_seed", default=42, min=0, max=0xFFFFFFFFFFFFFFFF),
                io.Int.Input("steps", default=6, min=4, max=20),
            ],
            outputs=[
                io.Image.Output(display_name="COLLAGE"),
                io.Image.Output(display_name="SHOTS"),
            ],
        )

    @classmethod
    def execute(cls, model, clip, vae, prompt, width, height, noise_seed, steps) -> io.NodeOutput:
        marked = _count_shot_markers(prompt)
        n = marked if marked >= 1 else 10
        cols, rows = _grid_for_shots(n, 5, 2)
        scripts = _parse_scripts(prompt, n)
        length = align_frame_count(5)  # 最短，只取静帧
        sampler = _uw(KSamplerSelect.execute(sampler_name="res_multistep"))[0]
        tiles = []
        for i, script in enumerate(scripts):
            print(f"[Drama10/SB] still {i+1}/{n}", flush=True)
            # 强调单帧构图
            p = "静态分镜概念图，一张完整单镜头画面，" + script
            imgs = _gen_one_shot(
                model, clip, vae, p, width, height, length, None, int(noise_seed) + i, steps, sampler
            )
            # 取末帧：首帧常糊脸/扭曲（竖屏分镜静帧）
            if imgs.dim() == 4:
                frame = imgs[-1:]
            else:
                frame = imgs[-1:1]
            tiles.append(frame.contiguous())
            comfy.model_management.soft_empty_cache()
        collage = _make_collage(tiles, cols, rows)
        shots = torch.cat(tiles, dim=0)
        return io.NodeOutput(collage, shots)


class Drama10ShotMiniMaxH3(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Drama10ShotMiniMaxH3",
            display_name="短剧10镜 Image to Video (MiniMax H3)",
            category="video/minimax",
            description="优先用 Comfy input 下单镜 PNG 目录成片；否则自动生成分镜静帧。统一旁白+BGM，镜头交叉淡化。",
            inputs=[
                io.Model.Input("model"),
                io.Clip.Input("clip"),
                io.Vae.Input("vae", tooltip="Video VAE"),
                io.Vae.Input("audio_vae", tooltip="Audio VAE（本版成片不再用分镜环境音）"),
                io.Image.Input(
                    "first_frame",
                    optional=True,
                    tooltip="（可选，已弃用）上传多格拼图；请改用 shot_stills_subdir 单镜目录。",
                ),
                io.String.Input(
                    "shot_stills_subdir",
                    default="",
                    tooltip="相对 Comfy input 的目录，内含 shot_01.png …；与 auto_storyboard=False 联用。",
                ),
                io.String.Input("prompt", multiline=True, default=""),
                io.Int.Input("width", default=352, min=32, max=nodes.MAX_RESOLUTION, step=32),
                io.Int.Input("height", default=608, min=32, max=nodes.MAX_RESOLUTION, step=32),
                io.Float.Input(
                    "shot_duration",
                    default=6.0,
                    min=2.0,
                    max=12.0,
                    step=0.5,
                    tooltip="每一镜秒数（10×6≈60秒）",
                ),
                io.Int.Input("noise_seed", default=42, min=0, max=0xFFFFFFFFFFFFFFFF),
                io.Boolean.Input("turbo_mode", default=True),
                io.Int.Input("steps", default=8, min=4, max=40),
                io.Int.Input("grid_cols", default=5, min=1, max=10),
                io.Int.Input("grid_rows", default=2, min=1, max=10),
                io.Boolean.Input(
                    "auto_storyboard",
                    default=True,
                    tooltip="无 shot_stills_subdir 时，自动用脚本在 Comfy 生成分镜静帧再 i2v。",
                ),
                io.Boolean.Input(
                    "resume",
                    default=True,
                    tooltip="断点续跑：复用已生成的分镜/镜头缓存，崩溃后重跑会从断点继续。",
                ),
                io.Int.Input(
                    "single_shot_index",
                    default=0,
                    min=0,
                    max=24,
                    tooltip="0=全部镜头；1..N=仅跑该镜 i2v（分镜拆分模式）。",
                ),
                io.Boolean.Input(
                    "finalize_mux",
                    default=True,
                    tooltip="False=只生成镜头不拼接；True=拼接+混音出成片。",
                ),
                io.Boolean.Input(
                    "preview_with_audio",
                    default=True,
                    tooltip="单镜模式时为本镜 mp4 混入口播+BGM+环境声。",
                ),
            ],
            outputs=[io.Video.Output(display_name="VIDEO"), io.Image.Output(display_name="STORYBOARD")],
        )

    @classmethod
    def execute(
        cls,
        model,
        clip,
        vae,
        audio_vae,
        prompt,
        width,
        height,
        shot_duration,
        noise_seed,
        turbo_mode,
        steps,
        grid_cols,
        grid_rows,
        first_frame=None,
        shot_stills_subdir="",
        auto_storyboard=True,
        resume=True,
        single_shot_index=0,
        finalize_mux=True,
        preview_with_audio=True,
    ) -> io.NodeOutput:
        width, height = _adapt_canvas_minimax(int(width), int(height))
        print(f"[Drama10] canvas {width}x{height}", flush=True)
        shot_only = int(single_shot_index or 0)
        preview_one_shot = shot_only > 0 and not bool(finalize_mux)
        block_shot_nums: list[int] = []

        if preview_one_shot:
            one_block = _block_for_shot_num(prompt, shot_only)
            if _is_black_shot_text(one_block):
                raise ValueError(f"镜 {shot_only} 为黑场，无法单镜图生视频")
            blocks = [one_block]
            n_shots = 1
            cols, rows = 1, 1
            block_shot_nums = [int(shot_only)]
            scripts = _parse_scripts(one_block, 1)
            voice_per_shot = [_parse_voice_lines(one_block)]
            print(
                f"[Drama10] SINGLE-SHOT setup sn={shot_only} "
                f"→ still shot_{int(shot_only):02d}.png (not index 1)",
                flush=True,
            )
        else:
            marked = _count_shot_markers(prompt)
            n_shots = marked if marked >= 2 else max(1, int(grid_cols) * int(grid_rows))
            cols, rows = _grid_for_shots(n_shots, grid_cols, grid_rows)
            blocks = _shot_blocks(prompt, n_shots)
            block_shot_nums = [
                _shot_num_from_block(b) or (i + 1) for i, b in enumerate(blocks)
            ]
            scripts = _parse_scripts(prompt, n_shots)
            voice_per_shot = [_parse_voice_lines(b) for b in blocks]
        length = align_frame_count(max(5, int(round(float(shot_duration) * FPS))))
        shot_sec = length / float(FPS)
        n_steps = int(steps)
        sampler = _uw(KSamplerSelect.execute(sampler_name="res_multistep"))[0]

        job = _job_key(prompt, int(noise_seed), int(width), int(height), int(length), int(n_steps), int(n_shots))
        ckpt = _ckpt_dir(job)
        print(f"[Drama10] ckpt={ckpt} resume={resume}", flush=True)
        if resume:
            _import_tmp_shot_cache(ckpt)

        meta = {
            "job": job,
            "n_shots": n_shots,
            "width": int(width),
            "height": int(height),
            "length": int(length),
            "steps": int(n_steps),
            "seed": int(noise_seed),
        }
        (ckpt / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        storyboard_path = ckpt / "storyboard.png"
        storyboard_img = None
        tiles = []
        sig_stub = ckpt / "_shot_stills_sig.md5"
        used_shot_stills = False

        shot_stills_subdir = (shot_stills_subdir or "").strip().replace("\\", "/")
        use_upload = first_frame is not None and _looks_like_collage(first_frame, cols, rows)
        if first_frame is not None and not use_upload:
            print("[Drama10] 忽略无效默认/单图输入，改为单镜目录或自动分镜", flush=True)

        if shot_stills_subdir and not auto_storyboard:
            used_shot_stills = True
            print(
                f"[Drama10] using per-shot stills from input/{shot_stills_subdir}",
                flush=True,
            )
            tiles = _load_tiles_from_shot_stills(
                shot_stills_subdir,
                n_shots,
                int(width),
                int(height),
                shot_nums=block_shot_nums,
            )
            print(
                f"[Drama10] stills loaded for shot_nums={block_shot_nums}",
                flush=True,
            )
            storyboard_img = (
                _make_collage(tiles, cols, rows)
                if len(tiles) >= cols * rows
                else tiles[0]
            )
            from PIL import Image as PILImage

            t = storyboard_img[0].detach().cpu().float().clamp(0, 1).numpy()
            PILImage.fromarray((t[..., :3] * 255).astype(np.uint8)).save(storyboard_path)
            sig_stub.write_text(_shot_stills_md5(shot_stills_subdir, n_shots), encoding="utf-8")
        elif use_upload:
            print(f"[Drama10] using uploaded collage, grid={cols}x{rows}", flush=True)
            tiles = _split_grid(first_frame, cols, rows, inset_ratio=0.0)[:n_shots]
            tiles = [_resize_tile(t, int(width), int(height)) for t in tiles]
            storyboard_img = first_frame[:1]
            _save_storyboard_png(storyboard_img, f"drama_ui/ckpt/{job}/storyboard_upload")
            # also fixed name for resume
            from PIL import Image as PILImage

            t = storyboard_img[0].detach().cpu().float().clamp(0, 1).numpy()
            PILImage.fromarray((t[..., :3] * 255).astype(np.uint8)).save(storyboard_path)
        elif resume and storyboard_path.exists():
            print(f"[Drama10] resume storyboard from {storyboard_path}", flush=True)
            storyboard_img = _load_png_tensor(storyboard_path)
            tiles = [
                _resize_tile(t, int(width), int(height))
                for t in _split_grid(storyboard_img, cols, rows, inset_ratio=0.0)[:n_shots]
            ]
        elif auto_storyboard:
            print("[Drama10] no collage → auto storyboard stills", flush=True)
            still_len = align_frame_count(5)
            for i, script in enumerate(scripts):
                tile_path = ckpt / f"tile_{i+1:02d}.pt"
                if resume and tile_path.exists():
                    print(f"[Drama10] resume still {i+1}/{n_shots}", flush=True)
                    frame = _load_shot_pt(tile_path)[:1]
                else:
                    print(f"[Drama10] auto-still {i+1}/{n_shots}", flush=True)
                    p = "静态分镜概念图，一张完整单镜头画面，东方玄幻电影感，真人写实，" + script
                    imgs = _gen_one_shot(
                        model,
                        clip,
                        vae,
                        p,
                        width,
                        height,
                        still_len,
                        None,
                        int(noise_seed) + 1000 + i,
                        max(4, n_steps - 2),
                        sampler,
                    )
                    frame = imgs[:1] if imgs.dim() == 4 else imgs[0:1]
                    frame = frame.contiguous().detach().cpu()
                    _save_shot_pt(tile_path, frame)
                    comfy.model_management.soft_empty_cache()
                tiles.append(frame)
            storyboard_img = _make_collage(tiles, cols, rows)
            from PIL import Image as PILImage

            t = storyboard_img[0].detach().cpu().float().clamp(0, 1).numpy()
            PILImage.fromarray((t[..., :3] * 255).astype(np.uint8)).save(storyboard_path)
            _save_storyboard_png(storyboard_img, "drama_ui/auto_storyboard")
        else:
            raise ValueError("未上传分镜拼图，且 auto_storyboard=False。")

        stills_md5 = ""
        if used_shot_stills:
            stills_md5 = _shot_stills_md5(shot_stills_subdir, n_shots)
        if used_shot_stills and sig_stub.is_file():
            md5 = sig_stub.read_text(encoding="utf-8").strip()
            fake = ckpt / f"_stills_{md5}.png"
            if not fake.is_file() and storyboard_path.is_file():
                fake.write_bytes(storyboard_path.read_bytes())
            _sync_ckpt_source_sig(
                ckpt, fake if fake.is_file() else storyboard_path, prompt, stills_md5
            )
        else:
            _sync_ckpt_source_sig(ckpt, storyboard_path, prompt, stills_md5)

        shot_durations = _parse_shot_durations(blocks, float(shot_duration))
        while len(shot_durations) < len(tiles):
            shot_durations.append(float(shot_duration))
        shot_durations = shot_durations[: len(tiles)]

        ambience_per_shot = [_parse_ambience_line(b) for b in blocks]
        tiles, scripts, voice_per_shot, shot_durations, orig_shot_nums, ambience_per_shot = (
            _drop_black_shots_for_video(
                tiles,
                scripts,
                voice_per_shot,
                shot_durations,
                ambience_per_shot,
                blocks,
            )
        )
        lip_blocks = []
        for i, _orig in enumerate(orig_shot_nums):
            lip_blocks.append(blocks[i] if i < len(blocks) else "")

        # shot_only / preview_one_shot 已在函数开头设定
        if preview_one_shot:
            try:
                idx = orig_shot_nums.index(shot_only)
            except ValueError as e:
                raise ValueError(
                    f"单镜模式：脚本/分镜中找不到镜号 {shot_only}（orig={orig_shot_nums}）"
                ) from e
            tiles = [tiles[idx]]
            scripts = [scripts[idx]]
            voice_per_shot = [voice_per_shot[idx]]
            shot_durations = [shot_durations[idx]]
            ambience_per_shot = [ambience_per_shot[idx]]
            lip_blocks = [lip_blocks[idx]]
            orig_shot_nums = [shot_only]
            print(
                f"[Drama10] SINGLE-SHOT mode sn={shot_only} (not full episode; 1 tile)",
                flush=True,
            )

        print(
            f"[Drama10] plan shots={len(tiles)} orig_nums={orig_shot_nums} "
            f"durations={shot_durations} total≈{sum(shot_durations):.1f}s steps={n_steps}",
            flush=True,
        )
        for orig_sn, vl, sc in zip(orig_shot_nums, voice_per_shot, scripts):
            print(f"[Drama10] voice shot{orig_sn}: {vl}", flush=True)
            sc_prev = (sc or "").replace("\r", "").strip()
            if len(sc_prev) > 280:
                sc_prev = sc_prev[:280] + "…"
            print(
                f"[Drama10] video script shot{orig_sn} ({len(sc or '')} chars, from prompt 视频提示词): {sc_prev}",
                flush=True,
            )

        # 音频可缓存（单镜预览成片不拼整集，跳过整段 TTS+BGM 以省时）
        audio_pt = ckpt / "audio.pt"
        audio = None
        want_preview_audio = _coerce_bool(preview_with_audio) and preview_one_shot
        if os.environ.get("DRAMA10_WEB_AUDIO_ONLY", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            want_preview_audio = False
        if preview_one_shot and not want_preview_audio:
            print(
                "[Drama10] single-shot silent video (Web 独占混音，无 Comfy 合成底噪)",
                flush=True,
            )
        elif preview_one_shot:
            print("[Drama10] single-shot preview: will mux audio after i2v", flush=True)
        else:
            print(
                "[Drama10] defer TTS/BGM until mux (ffprobe shot lengths + start_sec)",
                flush=True,
            )

        i2v_steps = max(4, min(n_steps, 6)) if preview_one_shot else n_steps
        if preview_one_shot and i2v_steps != n_steps:
            print(f"[Drama10] preview i2v steps={i2v_steps} (full={n_steps})", flush=True)

        run_i2v = finalize_mux or shot_only > 0
        if run_i2v:
            # 逐镜生成：只写盘，不在内存里累计成片（避免 OOM 杀进程）
            for i, (tile, script) in enumerate(zip(tiles, scripts)):
                orig_sn = int(orig_shot_nums[i])
                if shot_only > 0 and orig_sn != shot_only:
                    continue
                dur_i = float(shot_durations[i]) if i < len(shot_durations) else float(shot_duration)
                length_i = align_frame_count(max(5, int(round(dur_i * FPS))))
                sp = _shot_pt_path(ckpt, orig_sn)
                mp4 = _shot_mp4_path(ckpt, orig_sn)
                if mp4.is_file() and mp4.stat().st_size > 8192:
                    if resume or not sp.is_file():
                        print(
                            f"[Drama10] resume mp4 shot {orig_sn} <- {mp4.name}",
                            flush=True,
                        )
                        continue
                if _try_recover_shot_mp4(ckpt, orig_sn, float(FPS)):
                    continue
                if resume and sp.is_file() and sp.stat().st_size > 1024:
                    print(
                        f"[Drama10] resume i2v shot {orig_sn} ({i+1}/{len(tiles)}) <- {sp.name}",
                        flush=True,
                    )
                    continue
                tile = _resize_tile(tile, int(width), int(height))
                print(
                    f"[Drama10] i2v shot {orig_sn} ({i+1}/{len(tiles)}) "
                    f"{length_i}f/{dur_i:.1f}s tile={tuple(tile.shape)}",
                    flush=True,
                )
                lip_blk = lip_blocks[i] if i < len(lip_blocks) else ""
                lip_hint = _i2v_lip_sync_hint(lip_blk)
                amb_i2v = _parse_ambience_line(lip_blk)
                amb_bit = (
                    f"\n【环境动效】{amb_i2v}；画面须与上述环境声一致（粒子、旗帜、人群等）。"
                    if amb_i2v
                    else ""
                )
                i2v_prompt = (script or "").strip() + (
                    amb_bit
                    + "\n【口型】"
                    + lip_hint
                    + "\n【图生视频硬性要求】同一张实拍摄影机连续拍摄的镜头：人物与环境同一曝光、"
                    "同一光学景深链路、统一环境光与色温；须有接触阴影与互相遮挡；"
                    "严禁抠像合成、严禁人物前景硬边贴背景、严禁绿幕感/描边光晕/分层合成/贴纸感。"
                    "摄影机运动平稳；人物呼吸眨眼与环境粒子同步微动。"
                    "严禁旋转、甩镜、扭曲、万花筒、分格拼图；地平线水平，画面 upright。"
                    + I2V_NO_TEXT_SUFFIX
                )
                try:
                    sp_txt = ckpt / f"i2v_prompt_shot{orig_sn:02d}.txt"
                    sp_txt.write_text(i2v_prompt, encoding="utf-8")
                except OSError as e:
                    print(f"[Drama10] i2v script save skip: {e}", flush=True)
                prev = i2v_prompt.replace("\r", "")
                if len(prev) > 520:
                    prev = prev[:520] + "…"
                print(
                    f"[Drama10] i2v script shot{orig_sn} ({len(i2v_prompt)} chars):\n{prev}",
                    flush=True,
                )
                tile_in = _resize_tile(tile, int(width), int(height))
                imgs = _gen_one_shot(
                    model,
                    clip,
                    vae,
                    i2v_prompt,
                    width,
                    height,
                    length_i,
                    tile_in,
                    int(noise_seed) + orig_sn - 1,
                    i2v_steps,
                    sampler,
                )
                imgs = _correct_i2v_orientation(
                    tile_in, imgs, int(width), int(height), orig_sn=orig_sn
                )
                if imgs.dtype != torch.float16:
                    imgs = _as_thwc(imgs).detach().cpu().half().contiguous()
                _save_shot_pt(sp, imgs)
                _release_vram_after_sampling()
                _encode_shot_mp4(sp, mp4, float(FPS))
                del imgs
                gc.collect()
                comfy.model_management.soft_empty_cache()

        if not finalize_mux:
            mp4_dir = ckpt / "mp4"
            mp4_dir.mkdir(exist_ok=True)
            if shot_only > 0 and shot_only not in orig_shot_nums:
                print(
                    f"[Drama10] single-shot {shot_only} skipped "
                    f"(黑场或未入成片镜；有效镜号 {orig_shot_nums})",
                    flush=True,
                )
                stub = mp4_dir / f"shot_{orig_shot_nums[-1]:02d}.mp4"
                sb_out = _storyboard_for_output(storyboard_img, tiles)
                return io.NodeOutput(VideoFromFile(str(stub)), sb_out)
            orig_sn = int(shot_only) if shot_only > 0 else int(orig_shot_nums[0])
            pt = _shot_pt_path(ckpt, orig_sn)
            mp4 = mp4_dir / f"shot_{orig_sn:02d}.mp4"
            if pt.is_file():
                _encode_shot_mp4(pt, mp4, float(FPS))
            elif not (resume and mp4.is_file() and mp4.stat().st_size > 1024):
                raise RuntimeError(f"单镜 {shot_only} 未生成 mp4: {mp4}")
            print(f"[Drama10] single-shot segment done (no mux): {mp4.name}", flush=True)
            try:
                comfy.model_management.unload_all_models()
            except Exception:
                pass
            comfy.model_management.soft_empty_cache()
            if not mp4.is_file():
                raise RuntimeError(f"单镜 {shot_only} 未生成 mp4: {mp4}")
            if want_preview_audio:
                if _mp4_has_audio_stream(mp4):
                    print(
                        f"[Drama10] single-shot {orig_sn} mp4 already has audio, skip remux",
                        flush=True,
                    )
                else:
                    try:
                        comfy.model_management.unload_all_models()
                    except Exception:
                        pass
                    comfy.model_management.soft_empty_cache()
                    gc.collect()
                    i_ep = (
                        orig_shot_nums.index(orig_sn)
                        if orig_sn in orig_shot_nums
                        else max(0, orig_sn - 1)
                    )
                    vl = voice_per_shot[i_ep] if i_ep < len(voice_per_shot) else []
                    amb = ambience_per_shot[i_ep] if i_ep < len(ambience_per_shot) else ""
                    print(
                        f"[Drama10] mux shot{orig_sn} voices={len(vl)} amb={bool(amb)}",
                        flush=True,
                    )
                    _mux_one_shot_preview_audio(
                        ckpt,
                        mp4,
                        vl,
                        amb,
                        int(noise_seed),
                    )
            sb_out = _storyboard_for_output(storyboard_img, tiles)
            return io.NodeOutput(VideoFromFile(str(mp4)), sb_out)

        # 释放显存后再 ffmpeg 封装，峰值内存低
        print("[Drama10] unload models before ffmpeg mux...", flush=True)
        try:
            comfy.model_management.unload_all_models()
        except Exception:
            pass
        comfy.model_management.soft_empty_cache()
        gc.collect()

        mp4_dir = ckpt / "mp4"
        mp4_dir.mkdir(exist_ok=True)
        shot_mp4s = []
        for i, orig_sn in enumerate(orig_shot_nums):
            orig_sn = int(orig_sn)
            pt = _shot_pt_path(ckpt, orig_sn)
            mp4 = mp4_dir / f"shot_{orig_sn:02d}.mp4"
            if not (resume and mp4.exists() and mp4.stat().st_size > 1024):
                _encode_shot_mp4(pt, mp4, float(FPS))
            else:
                print(f"[Drama10] resume mp4 {mp4.name}", flush=True)
            shot_mp4s.append(mp4)
            if mp4.is_file() and pt.is_file() and pt.stat().st_size > 1024 * 1024:
                try:
                    pt.unlink()
                    print(f"[Drama10] freed disk: removed {pt.name}", flush=True)
                except OSError:
                    pass

        silent = ckpt / "video_silent.mp4"
        use_xfade = os.environ.get("DRAMA10_USE_XFADE", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if use_xfade:
            _ffmpeg_concat_xfade(shot_mp4s, silent)
        else:
            _ffmpeg_concat_copy(shot_mp4s, silent)

        # 按成片时长裁切音频
        probe = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(silent),
            ],
            text=True,
        ).strip()
        dur = float(probe or (len(tiles) * shot_sec))
        shot_starts: list[float] = []
        actual_durs: list[float] = []
        acc = 0.0
        for mp4 in shot_mp4s:
            shot_starts.append(acc)
            d = _probe_duration_sec(mp4)
            actual_durs.append(d)
            acc += d
        print(
            f"[Drama10] audio sync: shot_starts={[round(x, 2) for x in shot_starts]} "
            f"durs={[round(x, 2) for x in actual_durs]}",
            flush=True,
        )

        speech, sub_events = _build_speech_timeline(
            voice_per_shot,
            shot_starts,
            actual_durs,
            episode_shot_indices=list(range(len(voice_per_shot))),
            episode_wide_sync=True,
        )
        bgm = _continuous_bgm(max(dur, acc) + 2.5, seed=int(noise_seed))
        sfx = _build_ambience_timeline(
            ambience_per_shot, actual_durs, seed=int(noise_seed) + 17
        )
        audio = _mix_audio(speech, bgm, sfx)
        del speech, bgm, sfx

        need = int(dur * SR)
        w = audio["waveform"]
        if w.dim() == 2:
            w = w.unsqueeze(0)
        if w.shape[-1] > need:
            w = w[:, :, :need]
        elif w.shape[-1] < need:
            w = torch.cat([w, torch.zeros(1, w.shape[1], need - w.shape[-1])], dim=-1)
        audio = {"waveform": w, "sample_rate": SR}
        wav_path = ckpt / "audio.wav"
        _write_wav(audio, wav_path)

        srt_path = ckpt / "subs.srt"
        _write_srt(sub_events, srt_path)
        burn_subs = os.environ.get("DRAMA10_BURN_SUBS", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        final_mp4 = Path(folder_paths.get_output_directory()) / "drama_ui" / f"ice_realm_ep1_{job}.mp4"
        final_mp4.parent.mkdir(parents=True, exist_ok=True)
        _mux_av(silent, wav_path, final_mp4, srt_path if burn_subs else None)

        # 标记完成
        (ckpt / "DONE").write_text(str(final_mp4), encoding="utf-8")
        video = VideoFromFile(str(final_mp4))
        if storyboard_img is None:
            storyboard_img = tiles[0] if tiles else _load_png_tensor(storyboard_path)
        print(f"[Drama10] final done: {final_mp4}", flush=True)
        return io.NodeOutput(video, storyboard_img)


class Drama10Extension(ComfyExtension):
    async def get_node_list(self):
        return [Drama10ShotMiniMaxH3, Drama10StoryboardMiniMaxH3]


async def comfy_entrypoint() -> Drama10Extension:
    return Drama10Extension()
