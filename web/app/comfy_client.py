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

    def _ws_base(self) -> str:
        if self.base.startswith("https://"):
            return "wss://" + self.base[len("https://") :]
        if self.base.startswith("http://"):
            return "ws://" + self.base[len("http://") :]
        return "ws://" + self.base

    def queue_prompt(self, prompt: Dict[str, Any]) -> str:
        client_id = str(uuid.uuid4())
        self._last_client_id = client_id
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
        self._last_prompt_id = str(pid)
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

    def _start_progress_listener(
        self, prompt_id: str, client_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], Callable[[], None]]:
        """Listen Comfy /ws progress (value/max) to mirror terminal tqdm timing."""
        state: Dict[str, Any] = {
            "value": 0,
            "max": 0,
            "step_times": [],
            "last_step_t": None,
            "t0": time.time(),
        }
        stop = threading.Event()
        cid = (client_id or getattr(self, "_last_client_id", None) or str(uuid.uuid4())).strip()
        ws_url = f"{self._ws_base()}/ws?clientId={cid}"
        token = self._auth_token()
        headers = [f"Authorization: Bearer {token}"]

        def _on_message(_ws: Any, message: Any) -> None:
            try:
                payload = json.loads(message)
            except (TypeError, ValueError, json.JSONDecodeError):
                return
            if not isinstance(payload, dict) or payload.get("type") != "progress":
                return
            data = payload.get("data") or {}
            pid = str(data.get("prompt_id") or "")
            # 部分 Comfy 版本 progress 不带 prompt_id；有则校验，无则照收
            if pid and str(prompt_id) and pid != str(prompt_id):
                return
            try:
                value = int(data.get("value") or 0)
                max_v = int(data.get("max") or 0)
            except (TypeError, ValueError):
                return
            if max_v <= 0:
                return
            now = time.time()
            prev_v = int(state.get("value") or 0)
            prev_max = int(state.get("max") or 0)
            # 新一轮采样（max 变化或从头开始）清空步时
            if max_v != prev_max or value < prev_v:
                state["step_times"] = []
                state["last_step_t"] = None
                state["t0"] = now
            last_t = state.get("last_step_t")
            if last_t is not None and value > prev_v:
                dt = now - float(last_t)
                if 0.05 < dt < 3600:
                    times = list(state.get("step_times") or [])
                    times.append(dt)
                    state["step_times"] = times[-10:]
            elif last_t is None and value >= 1:
                # 首帧：用从开始到现在估算单步，便于立刻出剩余时间
                dt0 = now - float(state.get("t0") or now)
                if value > 0 and 0.5 < dt0 < 3600:
                    state["step_times"] = [dt0 / float(value)]
            state["value"] = value
            state["max"] = max_v
            state["last_step_t"] = now

        def _runner() -> None:
            try:
                import websocket  # type: ignore  # websocket-client
            except ImportError:
                return
            backoff = 1.0
            while not stop.is_set():
                try:
                    ws = websocket.WebSocketApp(
                        ws_url,
                        header=headers,
                        on_message=_on_message,
                    )
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception:
                    pass
                if stop.is_set():
                    break
                time.sleep(backoff)
                backoff = min(8.0, backoff * 1.5)

        th = threading.Thread(target=_runner, name="comfy-progress-ws", daemon=True)
        th.start()

        def _stop() -> None:
            stop.set()

        return state, _stop

    @staticmethod
    def _progress_eta_s(state: Dict[str, Any]) -> Optional[float]:
        max_v = int(state.get("max") or 0)
        value = int(state.get("value") or 0)
        if max_v <= 0:
            return None
        remaining = max(0, max_v - value)
        if remaining == 0:
            return 0.0
        times = list(state.get("step_times") or [])
        if times:
            avg = sum(times) / len(times)
            return float(remaining * avg)
        t0 = float(state.get("t0") or 0)
        if value > 0 and t0 > 0:
            avg = max(0.1, (time.time() - t0) / value)
            return float(remaining * avg)
        return None

    def wait_prompt(
        self,
        prompt_id: str,
        timeout: float = 7200.0,
        poll_sec: float = 8.0,
        on_tick: Optional[Callable[[float, Dict[str, Any]], None]] = None,
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        t0 = time.time()
        comfy_down_since: Optional[float] = None
        idle_ticks = 0
        prog_state, stop_ws = self._start_progress_listener(prompt_id, client_id=client_id)
        try:
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
                    step = int(prog_state.get("value") or 0)
                    steps = int(prog_state.get("max") or 0)
                    ws_eta = self._progress_eta_s(prog_state)
                    log_info: Dict[str, Any] = {}
                    try:
                        from .comfy_log_progress import fetch_comfy_log_progress

                        log_info = fetch_comfy_log_progress() or {}
                    except Exception:
                        log_info = {}
                    # MiniMaxH3 外层 tqdm 常不进 /ws progress：优先日志步数/ETA
                    if int(log_info.get("steps") or 0) > 0 and steps <= 0:
                        step = int(log_info.get("step") or 0)
                        steps = int(log_info.get("steps") or 0)
                    payload: Dict[str, Any] = {
                        "running": len(running),
                        "pending": len(pending),
                        "step": step,
                        "steps": steps,
                        "eta_s": ws_eta,
                        "step_pct": int(100 * step / steps) if steps > 0 else 0,
                        "eta_source": "ws" if ws_eta is not None else "",
                    }
                    if log_info.get("eta_s") is not None:
                        payload["log_eta_s"] = float(log_info["eta_s"])
                        payload["eta_s"] = float(log_info["eta_s"])
                        payload["eta_source"] = "comfy_log"
                    if log_info.get("step") is not None and log_info.get("steps"):
                        payload["log_step"] = int(log_info["step"])
                        payload["log_steps"] = int(log_info["steps"])
                        payload["log_step_pct"] = int(log_info.get("step_pct") or 0)
                        # 有日志采样时用日志百分比展示更贴近终端
                        if int(log_info.get("steps") or 0) > 0:
                            payload["step"] = int(log_info["step"])
                            payload["steps"] = int(log_info["steps"])
                            payload["step_pct"] = int(
                                log_info.get("step_pct")
                                or int(
                                    100
                                    * int(log_info["step"])
                                    / float(log_info["steps"])
                                )
                            )
                    if log_info.get("raw_remain"):
                        payload["log_remain"] = str(log_info["raw_remain"])
                    on_tick(time.time() - t0, payload)
                time.sleep(poll_sec)
            raise TimeoutError(f"ComfyUI 超时 ({timeout}s): {prompt_id}")
        finally:
            stop_ws()

    def _auth_headers_no_json(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._auth_token()}"}

    def upload_image(
        self,
        local_path: Path,
        *,
        subfolder: str = "",
        overwrite: bool = True,
        image_type: str = "input",
    ) -> Dict[str, Any]:
        """上传到 Comfy input/output（云 GPU 隧道时替代本机拷贝）。"""
        path = Path(local_path)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        sub = (subfolder or "").strip().replace("\\", "/").strip("/")
        data = {
            "overwrite": "true" if overwrite else "false",
            "type": image_type or "input",
        }
        if sub:
            data["subfolder"] = sub
        with path.open("rb") as fh:
            files = {"image": (path.name, fh, "application/octet-stream")}
            with httpx.Client(timeout=300.0) as client:
                r = client.post(
                    f"{self.base}/upload/image",
                    headers=self._auth_headers_no_json(),
                    data=data,
                    files=files,
                )
                if r.status_code >= 400:
                    detail = r.text.strip().replace("\n", " ")[:500]
                    raise RuntimeError(
                        f"Comfy /upload/image {r.status_code}: {detail or path.name}"
                    )
                return r.json() if r.content else {"name": path.name, "subfolder": sub}

    def download_view(
        self,
        filename: str,
        *,
        subfolder: str = "",
        file_type: str = "output",
        dest: Path,
    ) -> Path:
        """经 /view 拉取远端 output/input 文件到本地 dest。"""
        params = {
            "filename": filename,
            "subfolder": (subfolder or "").strip("/"),
            "type": file_type or "output",
        }
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=600.0) as client:
            r = client.get(
                f"{self.base}/view",
                headers=self._auth_headers_no_json(),
                params=params,
            )
            if r.status_code >= 400:
                detail = r.text.strip().replace("\n", " ")[:300]
                raise RuntimeError(
                    f"Comfy /view {r.status_code}: {filename} · {detail}"
                )
            dest.write_bytes(r.content)
        return dest


def pick_first_saved_still(imgs: List[Path]) -> Optional[Path]:
    """SaveImage 多帧时取末帧（I2V 首帧常糊脸/扭曲，末帧更稳）。"""
    if not imgs:
        return None
    if len(imgs) == 1:
        return imgs[0]
    return sorted(imgs, key=lambda p: p.name)[-1]


def _history_media_items(history: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Yield (kind, item) for images / gifs / videos in history.outputs."""
    items: List[Tuple[str, Dict[str, Any]]] = []
    outs = history.get("outputs") or {}
    for node_out in outs.values():
        for item in node_out.get("images") or []:
            items.append(("image", item))
        for item in node_out.get("gifs") or []:
            items.append(("video", item))
        for item in node_out.get("videos") or []:
            items.append(("video", item))
    return items


def resolve_output_files(
    history: Dict[str, Any],
    *,
    subfolder_hint: str = "",
    client: Optional["ComfyClient"] = None,
) -> Tuple[List[Path], List[Path]]:
    """从 history.outputs 解析 SaveImage / SaveVideo；远端缺失时经 /view 拉取到本地镜像。"""
    images: List[Path] = []
    videos: List[Path] = []
    out_root = comfy_output_dir()
    out_root.mkdir(parents=True, exist_ok=True)
    fetcher = client
    for kind, item in _history_media_items(history):
        name = (item.get("filename") or "").strip()
        if not name:
            continue
        sub = (item.get("subfolder") or "").strip("/")
        if subfolder_hint and subfolder_hint not in sub and subfolder_hint not in name:
            pass
        fp = out_root / sub / name if sub else out_root / name
        if not fp.is_file() or fp.stat().st_size < 32:
            if fetcher is None:
                try:
                    fetcher = ComfyClient()
                except Exception:
                    fetcher = None
            if fetcher is not None:
                try:
                    ftype = (item.get("type") or "output").strip() or "output"
                    fetcher.download_view(
                        name, subfolder=sub, file_type=ftype, dest=fp
                    )
                except Exception:
                    pass
        if not fp.is_file():
            continue
        if kind == "image":
            images.append(fp)
        else:
            videos.append(fp)
    return images, videos


def find_newest_under(prefix: str, ext: str) -> Optional[Path]:
    """在 output 下按 mtime 找最新匹配 prefix 的文件。"""
    out_root = comfy_output_dir()
    if not out_root.is_dir():
        return None
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
    """裁剪铺满目标画幅（会切边，视频拼图等仍可用）。"""
    from PIL import Image

    tw, th = target_width, target_height
    iw, ih = im.size
    if iw < 1 or ih < 1:
        return im
    target_ratio = tw / th
    current_ratio = iw / ih
    portrait = th > tw
    if abs(current_ratio - target_ratio) > 0.005:
        if current_ratio > target_ratio:
            new_w = max(1, int(round(ih * target_ratio)))
            left = (iw - new_w) // 2
            im = im.crop((left, 0, left + new_w, ih))
        else:
            new_h = max(1, int(round(iw / target_ratio)))
            top = 0 if portrait else (ih - new_h) // 2
            if top + new_h > ih:
                top = max(0, ih - new_h)
            im = im.crop((0, top, iw, top + new_h))
    if im.size != (tw, th):
        im = im.resize((tw, th), _pillow_lanczos())
    return im


def _contain_to_aspect(im, target_width: int, target_height: int):
    """完整装入目标画幅（不裁切人物，不足处留边）。分镜静帧默认用这个。"""
    from PIL import Image

    tw, th = int(target_width), int(target_height)
    iw, ih = im.size
    if iw < 1 or ih < 1 or tw < 1 or th < 1:
        return im
    scale = min(tw / iw, th / ih)
    nw = max(1, int(round(iw * scale)))
    nh = max(1, int(round(ih * scale)))
    resized = im.resize((nw, nh), _pillow_lanczos())
    canvas = Image.new("RGB", (tw, th), (10, 14, 20))
    # 竖屏略靠上，减少「头顶被视觉裁掉」感；横屏居中
    x = (tw - nw) // 2
    y = max(0, (th - nh) // 5) if th > tw else (th - nh) // 2
    if y + nh > th:
        y = max(0, th - nh)
    canvas.paste(resized, (x, y))
    return canvas


def finalize_storyboard_tile(
    path: Path,
    target_width: int,
    target_height: int,
    *,
    skip_rotation: bool = False,
) -> bool:
    """分镜单镜落盘：EXIF + 方向校正 + 完整装入项目分辨率（不切人物）。"""
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
    # 上游常出 2:3，项目是 9:16；cover 会左右裁人，改为 contain 留边
    # 注意：API 若误用横屏 size，应在请求侧改正，勿对「横构图直立人物」做 90° 旋转（会躺倒）
    im = _contain_to_aspect(im, target_width, target_height)
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


# ---- 按后台「视频生成后端」选择 PPIO 或本机 Comfy ----
_LocalComfyClient = ComfyClient


class ComfyClient(_LocalComfyClient):  # type: ignore[no-redef]
    def __new__(cls, base_url: Optional[str] = None):
        if cls is ComfyClient and base_url is None:
            try:
                from .studio_settings import use_ppio_video
                from .ppio_client import PpioComfyClient

                if use_ppio_video():
                    return PpioComfyClient()
            except Exception:
                pass
        return object.__new__(cls)

    def __init__(self, base_url: Optional[str] = None) -> None:
        if type(self).__name__ == "PpioComfyClient":
            return
        _LocalComfyClient.__init__(self, base_url=base_url)
