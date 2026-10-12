# -*- coding: utf-8 -*-
"""PPIO Async Serverless ComfyUI worker client.

Call key is `{endpoint_id}-{app_name}` (not display name alone).
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

def comfy_root() -> Path:
    return Path(os.environ.get("COMFYUI_ROOT", "/home/linux/ComfyUI"))


def comfy_output_dir() -> Path:
    return comfy_root() / "output"


def _ppio_base() -> str:
    try:
        from .studio_settings import ppio_async_base

        return ppio_async_base()
    except Exception:
        return os.environ.get(
            "PPIO_ASYNC_BASE", "https://async-public.serverless.ppinfra.com/v1"
        ).rstrip("/")


def ppio_enabled() -> bool:
    """后台选了 PPIO 且密钥齐全时启用。"""
    try:
        from .studio_settings import use_ppio_video

        return use_ppio_video()
    except Exception:
        return bool(os.environ.get("PPIO_API_KEY", "").strip()) and bool(_endpoint_key())


def _endpoint_key() -> str:
    try:
        from .studio_settings import ppio_endpoint_key

        return ppio_endpoint_key()
    except Exception:
        key = os.environ.get("PPIO_ENDPOINT_KEY", "").strip()
        if key:
            return key
        eid = os.environ.get("PPIO_ENDPOINT_ID", "").strip()
        app = os.environ.get("PPIO_APP_NAME", "").strip()
        if eid and app:
            return f"{eid}-{app}"
        return eid or os.environ.get("PPIO_ENDPOINT_NAME", "").strip()


def _api_key() -> str:
    try:
        from .studio_settings import ppio_api_key

        return ppio_api_key()
    except Exception:
        return os.environ.get("PPIO_API_KEY", "").strip()


def _find_output_node_id(workflow: Dict[str, Any]) -> str:
    preferred = (
        "SaveVideo",
        "VHS_VideoCombine",
        "SaveImage",
        "SaveAnimatedWEBP",
        "SaveAnimatedPNG",
    )
    for cls in preferred:
        for nid, node in workflow.items():
            if isinstance(node, dict) and node.get("class_type") == cls:
                return str(nid)
    for nid, node in workflow.items():
        if isinstance(node, dict) and str(node.get("class_type", "")).startswith("Save"):
            return str(nid)
    return str(list(workflow.keys())[-1])


class PpioComfyClient:
    """Drop-in subset of ComfyClient for PPIO async Comfy worker."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base = f"{_ppio_base()}/{_endpoint_key()}"
        self.root = comfy_root()
        self._pending_images: List[Dict[str, str]] = []
        self._job_cache: Dict[str, Dict[str, Any]] = {}

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        }

    def register_input_file(self, local_path: Path, remote_name: str) -> None:
        data = local_path.read_bytes()
        self._pending_images.append(
            {
                "name": remote_name.replace("\\", "/"),
                "image": base64.b64encode(data).decode("ascii"),
            }
        )

    def clear_pending_inputs(self) -> None:
        self._pending_images.clear()

    def ping(self) -> None:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(f"{self.base}/health", headers=self._headers())
            r.raise_for_status()

    def get_queue(self) -> Dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(f"{self.base}/health", headers=self._headers())
            r.raise_for_status()
            h = r.json()
        jobs = h.get("jobs") or {}
        in_prog = int(jobs.get("inProgress") or 0)
        in_q = int(jobs.get("inQueue") or 0)
        return {
            "queue_running": [None] * in_prog,
            "queue_pending": [None] * in_q,
            "ppio_health": h,
        }

    def queue_prompt(self, prompt: Dict[str, Any]) -> str:
        payload_input: Dict[str, Any] = {
            "workflow": prompt,
            "output_node_id": _find_output_node_id(prompt),
        }
        if self._pending_images:
            payload_input["images"] = list(self._pending_images)
            self._pending_images.clear()
        body = {"input": payload_input}
        with httpx.Client(timeout=120.0) as client:
            r = client.post(f"{self.base}/run", headers=self._headers(), json=body)
            if r.status_code >= 400:
                detail = r.text.strip().replace("\n", " ")[:800]
                raise RuntimeError(
                    f"{r.status_code} for PPIO /run"
                    + (f" · {detail}" if detail else "")
                )
            data = r.json()
        job_id = data.get("id")
        if not job_id:
            raise RuntimeError(f"PPIO /run 无 job id: {data}")
        self._job_cache[job_id] = {"status": data.get("status") or "PENDING"}
        return job_id

    def get_history(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        with httpx.Client(timeout=60.0) as client:
            r = client.get(f"{self.base}/status/{prompt_id}", headers=self._headers())
            r.raise_for_status()
            st = r.json()
        status = (st.get("status") or "").upper()
        self._job_cache[prompt_id] = st
        if status in ("PENDING", "IN_PROGRESS", "IN_QUEUE", "RUNNING", ""):
            return None
        if status in ("FAILED", "CANCELLED", "CANCELED", "TIMED_OUT", "ERROR"):
            err = st.get("error") or st.get("message") or st
            raise RuntimeError(f"PPIO job {status}: {err}"[:2000])
        if status != "COMPLETED" and not (st.get("output") or st.get("result")):
            return None
        return self._materialize_history(prompt_id, st)

    def _materialize_history(self, prompt_id: str, st: Dict[str, Any]) -> Dict[str, Any]:
        out_root = comfy_output_dir()
        job_dir = out_root / "ppio" / prompt_id
        job_dir.mkdir(parents=True, exist_ok=True)
        output = st.get("output") or st.get("result") or {}
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except Exception:
                output = {"raw": output}

        images_meta: List[Dict[str, Any]] = []
        videos_meta: List[Dict[str, Any]] = []

        def _save_bytes(name: str, raw: bytes, kind: str) -> None:
            dest = job_dir / Path(name.replace("\\", "/")).name
            dest.write_bytes(raw)
            rel = {
                "filename": dest.name,
                "subfolder": f"ppio/{prompt_id}",
                "type": "output",
            }
            if kind == "video" or dest.suffix.lower() in (".mp4", ".webm", ".gif"):
                videos_meta.append(rel)
            else:
                images_meta.append(rel)

        def _handle_item(item: Any) -> None:
            if not isinstance(item, dict):
                return
            filename = (
                item.get("filename")
                or item.get("name")
                or item.get("file_name")
                or f"out_{uuid.uuid4().hex[:8]}.png"
            )
            url = item.get("url") or item.get("image_url") or item.get("file_url")
            b64 = item.get("image") or item.get("data") or item.get("base64")
            if url:
                with httpx.Client(timeout=300.0, follow_redirects=True) as client:
                    rr = client.get(url)
                    rr.raise_for_status()
                    kind = (
                        "video"
                        if filename.lower().endswith((".mp4", ".webm", ".gif"))
                        else "image"
                    )
                    _save_bytes(filename, rr.content, kind)
            elif isinstance(b64, str) and b64:
                raw = base64.b64decode(b64.split(",")[-1])
                kind = (
                    "video"
                    if filename.lower().endswith((".mp4", ".webm", ".gif"))
                    else "image"
                )
                _save_bytes(filename, raw, kind)

        if isinstance(output, dict):
            for key in ("images", "files", "videos", "gifs"):
                for item in output.get(key) or []:
                    _handle_item(item)
            if output.get("url") or output.get("image"):
                _handle_item(output)
            nested = output.get("output")
            if isinstance(nested, dict):
                for key in ("images", "files", "videos"):
                    for item in nested.get(key) or []:
                        _handle_item(item)

        node_out: Dict[str, Any] = {}
        if images_meta:
            node_out["images"] = images_meta
        if videos_meta:
            node_out["gifs"] = videos_meta
            node_out["videos"] = videos_meta
        return {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {"ppio": node_out} if node_out else {},
            "ppio_raw": st,
        }

    def wait_prompt(
        self,
        prompt_id: str,
        timeout: float = 7200.0,
        poll_sec: float = 8.0,
        on_tick: Optional[Callable[[float, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        t0 = time.time()
        while time.time() - t0 < timeout:
            hist = self.get_history(prompt_id)
            if hist:
                return hist
            if on_tick:
                try:
                    q = self.get_queue()
                    on_tick(
                        time.time() - t0,
                        {
                            "running": len(q.get("queue_running") or []),
                            "pending": len(q.get("queue_pending") or []),
                        },
                    )
                except Exception:
                    on_tick(time.time() - t0, {"running": 0, "pending": 0})
            time.sleep(poll_sec)
        raise TimeoutError(f"PPIO 超时 ({timeout}s): {prompt_id}")
