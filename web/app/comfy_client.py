# -*- coding: utf-8 -*-
"""ComfyUI HTTP API（Bearer token，与 run_drama_pipeline.py 一致）。"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

# 全站 ComfyUI 串行，避免 GPU 队列冲突
COMFY_QUEUE_LOCK = threading.Lock()

UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE_V = "minimax_h3_video_vae_fp16.safetensors"
VAE_A = "minimax_h3_audio_vae_fp32.safetensors"
LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"


def comfy_root() -> Path:
    return Path(os.environ.get("COMFYUI_ROOT", "/home/linux/ComfyUI"))


def comfy_output_dir() -> Path:
    return comfy_root() / "output"


class ComfyClient:
    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base = (base_url or os.environ.get("COMFYUI_URL", "http://127.0.0.1:8000")).rstrip(
            "/"
        )
        self.root = comfy_root()
        self._token: Optional[str] = None

    def _auth_token(self) -> str:
        if self._token:
            return self._token
        env = os.environ.get("COMFYUI_TOKEN", "").strip()
        if env:
            self._token = env
            return self._token
        pw_path = Path(
            os.environ.get("COMFYUI_PASSWORD_FILE", str(self.root / "login" / "PASSWORD"))
        )
        self._token = pw_path.read_bytes().split(b"\n", 1)[0].decode("utf-8", "replace").strip()
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._auth_token()}", "Content-Type": "application/json"}

    def ping(self) -> None:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(f"{self.base}/system_stats", headers=self._headers())
            r.raise_for_status()

    def queue_prompt(self, prompt: Dict[str, Any]) -> str:
        client_id = str(uuid.uuid4())
        body = {"prompt": prompt, "client_id": client_id}
        with httpx.Client(timeout=120.0) as client:
            r = client.post(f"{self.base}/prompt", headers=self._headers(), json=body)
            if r.status_code >= 400:
                detail = r.text.strip().replace("\n", " ")[:800]
                raise RuntimeError(
                    f"{r.status_code} {r.reason_phrase} for url '{r.url}'"
                    + (f" · {detail}" if detail else "")
                )
            r.raise_for_status()
            data = r.json()
        pid = data.get("prompt_id")
        if not pid:
            raise RuntimeError(f"ComfyUI /prompt 无 prompt_id: {data}")
        return pid

    def get_history(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(f"{self.base}/history/{prompt_id}", headers=self._headers())
            r.raise_for_status()
            data = r.json()
        return data.get(prompt_id)

    def get_queue(self) -> Dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(f"{self.base}/queue", headers=self._headers())
            r.raise_for_status()
            return r.json()

    def wait_prompt(
        self,
        prompt_id: str,
        timeout: float = 7200.0,
        poll_sec: float = 8.0,
        on_tick: Optional[Callable[[float, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        t0 = time.time()
        comfy_down_since: Optional[float] = None
        idle_ticks = 0
        while time.time() - t0 < timeout:
            try:
                hist = self.get_history(prompt_id)
                if hist:
                    status = hist.get("status") or {}
                    if status.get("status_str") == "error":
                        raise RuntimeError(json.dumps(status, ensure_ascii=False)[:2000])
                    for msg in status.get("messages") or []:
                        if not (isinstance(msg, (list, tuple)) and msg):
                            continue
                        if msg[0] == "execution_error":
                            raise RuntimeError(str(msg)[:2000])
                        if msg[0] == "execution_interrupted":
                            raise RuntimeError(
                                "Comfy 执行被中断（execution_interrupted），将 checkpoint 续跑"
                            )
                    if status.get("completed") or hist.get("outputs"):
                        return hist
                q = self.get_queue()
                running = q.get("queue_running") or []
                pending = q.get("queue_pending") or []
                comfy_down_since = None
                if not running and not pending:
                    idle_ticks += 1
                else:
                    idle_ticks = 0
                if idle_ticks >= 5:
                    raise RuntimeError(
                        "ComfyUI 任务已从队列消失（可能 OOM/进程重启），将 checkpoint 续跑"
                    )
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                if comfy_down_since is None:
                    comfy_down_since = time.time()
                if time.time() - comfy_down_since > 900:
                    raise RuntimeError(
                        f"ComfyUI 不可达超过 15 分钟（{exc}）。"
                        "请检查 Comfy 是否崩溃并重启后，用「排队：本集成片」重试（已开 checkpoint 续跑）。"
                    ) from exc
            if on_tick:
                try:
                    q = self.get_queue()
                    running = q.get("queue_running") or []
                    pending = q.get("queue_pending") or []
                except httpx.HTTPError:
                    running, pending = [], []
                on_tick(time.time() - t0, {"running": len(running), "pending": len(pending)})
            time.sleep(poll_sec)
        raise TimeoutError(f"ComfyUI 超时 ({timeout}s): {prompt_id}")


def pick_first_saved_still(imgs: List[Path]) -> Optional[Path]:
    """SaveImage 多帧时取末帧（I2V 首帧常糊脸/扭曲，末帧更稳）。"""
    if not imgs:
        return None
    if len(imgs) == 1:
        return imgs[0]
    return sorted(imgs, key=lambda p: p.name)[-1]


def resolve_output_files(
    history: Dict[str, Any],
    *,
    subfolder_hint: str = "",
) -> Tuple[List[Path], List[Path]]:
    """从 history.outputs 解析 SaveImage / SaveVideo 落盘路径。"""
    images: List[Path] = []
    videos: List[Path] = []
    out_root = comfy_output_dir()
    outs = history.get("outputs") or {}
    for node_out in outs.values():
        for item in node_out.get("images") or []:
            sub = (item.get("subfolder") or "").strip("/")
            if subfolder_hint and subfolder_hint not in sub and subfolder_hint not in item.get(
                "filename", ""
            ):
                pass
            fp = out_root / sub / item["filename"]
            if fp.is_file():
                images.append(fp)
        for item in node_out.get("gifs") or []:
            sub = (item.get("subfolder") or "").strip("/")
            fp = out_root / sub / item["filename"]
            if fp.is_file():
                videos.append(fp)
        for item in node_out.get("videos") or []:
            sub = (item.get("subfolder") or "").strip("/")
            fp = out_root / sub / item["filename"]
            if fp.is_file():
                videos.append(fp)
    return images, videos


def find_newest_under(prefix: str, ext: str) -> Optional[Path]:
    """在 output 下按 mtime 找最新匹配 prefix 的文件。"""
    out_root = comfy_output_dir()
    candidates: List[Path] = []
    for p in out_root.rglob(f"*{ext}"):
        if prefix.replace("\\", "/") in str(p.relative_to(out_root)).replace("\\", "/"):
            candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.stat().st_mtime)


def copy_into_project(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return dest


def split_collage(
    collage: Path,
    out_dir: Path,
    cols: int,
    rows: int,
    n_shots: int,
) -> List[Path]:
    """将分镜拼图切成 shot_01.png …"""
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("需要 Pillow：pip install pillow") from e

    out_dir.mkdir(parents=True, exist_ok=True)
    im = Image.open(collage).convert("RGB")
    w, h = im.size
    cw, ch = w // cols, h // rows
    pad = 0
    paths: List[Path] = []
    idx = 1
    for r in range(rows):
        for c in range(cols):
            if idx > n_shots:
                break
            box = (
                c * cw + pad,
                r * ch + pad,
                (c + 1) * cw - pad,
                (r + 1) * ch - pad,
            )
            tile = im.crop(box)
            path = out_dir / f"shot_{idx:02d}.png"
            tile.save(path)
            paths.append(path)
            idx += 1
    return paths


def manual_rotate_portrait_shot(
    path: Path, target_width: int, target_height: int, degrees: int = -90
) -> bool:
    """用户手动：每次顺时针 90°，不二次自动旋转。"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    if not path.is_file():
        return False
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.rotate(degrees, expand=True).save(path)
    return True


def storyboard_tile_rotate_degrees() -> int:
    """仅当 STORYBOARD_TILE_ROTATE 非 0 时强制旋转；默认 0（已是竖图不再转）。"""
    return int(os.environ.get("STORYBOARD_TILE_ROTATE", "0"))


def _pillow_lanczos():
    from PIL import Image

    resampling = getattr(Image, "Resampling", None)
    return resampling.LANCZOS if resampling else Image.LANCZOS


def _cover_to_aspect(im, target_width: int, target_height: int):
    """居中裁剪到目标宽高比，再缩放（避免旋转后拉伸脸）。"""
    from PIL import Image

    tw, th = target_width, target_height
    iw, ih = im.size
    if iw < 1 or ih < 1:
        return im
    target_ratio = tw / th
    current_ratio = iw / ih
    if abs(current_ratio - target_ratio) > 0.005:
        if current_ratio > target_ratio:
            new_w = max(1, int(round(ih * target_ratio)))
            left = (iw - new_w) // 2
            im = im.crop((left, 0, left + new_w, ih))
        else:
            new_h = max(1, int(round(iw / target_ratio)))
            top = (ih - new_h) // 2
            im = im.crop((0, top, iw, top + new_h))
    if im.size != (tw, th):
        im = im.resize((tw, th), _pillow_lanczos())
    return im


def finalize_storyboard_tile(
    path: Path,
    target_width: int,
    target_height: int,
    *,
    skip_rotation: bool = False,
) -> bool:
    """分镜单镜落盘：EXIF + 模型固定校正 + 缩放到项目分辨率。"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    if not path.is_file():
        return False
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = im.size
    portrait_target = target_height > target_width
    if not skip_rotation:
        rot = storyboard_tile_rotate_degrees()
        if portrait_target and w > h * 1.05:
            im = im.rotate(-90, expand=True)
        elif rot:
            im = im.rotate(rot, expand=True)
    im = _cover_to_aspect(im, target_width, target_height)
    im.save(path)
    return True


def fix_portrait_orientation(path: Path, target_width: int, target_height: int) -> bool:
    """上传图等：仅 EXIF + 横图转竖幅（不用分镜统一旋转）。"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    if not path.is_file():
        return False
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = im.size
    changed = False
    if target_height > target_width and w > h:
        im = im.rotate(-90, expand=True)
        changed = True
    if changed:
        im.save(path)
    return changed


def reprocess_storyboard_episode(
    project_id: str,
    ep_num: int,
    n_shots: int,
    target_width: int,
    target_height: int,
) -> None:
    """对 episodes/…/shots 下已有 PNG 重新套用 finalize（无需重跑 Comfy）。"""
    from . import db

    ep_tag = f"ep{ep_num:02d}"
    ep_dir = db.project_dir(project_id) / "episodes" / ep_tag
    shots_dir = ep_dir / "shots"
    touched = 0
    for i in range(1, n_shots + 1):
        p = shots_dir / f"shot_{i:02d}.png"
        if p.is_file():
            finalize_storyboard_tile(p, target_width, target_height)
            touched += 1
    if touched < 1:
        raise FileNotFoundError(f"无单镜分镜图：{shots_dir}")


def rebuild_collage_from_shots(
    shots_dir: Path,
    collage_path: Path,
    cols: int,
    rows: int,
    n_shots: int,
) -> None:
    from PIL import Image

    tiles = []
    tw, th = 0, 0
    for i in range(1, n_shots + 1):
        p = shots_dir / f"shot_{i:02d}.png"
        if p.is_file():
            im = Image.open(p).convert("RGB")
            if not tw:
                tw, th = im.size
            tiles.append(im)
        else:
            tiles.append(None)
    if not tw:
        tw, th = 720, 1280
    for idx, tile in enumerate(tiles):
        if tile is None:
            tiles[idx] = Image.new("RGB", (tw, th), (0, 0, 0))
        elif tile.size != (tw, th):
            tiles[idx] = tile.resize((tw, th), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (cols * tw, rows * th))
    idx = 0
    for r in range(rows):
        for c in range(cols):
            if idx >= len(tiles):
                break
            canvas.paste(tiles[idx], (c * tw, r * th))
            idx += 1
    collage_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(collage_path)
