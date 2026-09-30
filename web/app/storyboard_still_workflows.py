# -*- coding: utf-8 -*-
"""分镜静帧工作流：与成片 MiniMax H3 分离，可切换为 SDXL 等纯文生图。"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .drama10_workflows import (
    build_single_shot_still_workflow as _build_minimax_single,
    build_storyboard_workflow as _build_minimax_collage,
)
from .generation_prefs import parse_prefs
from .storyboard_image_api import BACKEND_IMAGE_API, image_api_configured

BACKEND_MINIMAX = "minimax_h3"
BACKEND_SDXL = "sdxl_t2i"

BACKEND_LABELS = {
    BACKEND_MINIMAX: "MiniMax H3（逐镜静帧，与成片同模型）",
    BACKEND_SDXL: "SDXL 文生图（竖屏剧照，需 Comfy 安装 checkpoint）",
    BACKEND_IMAGE_API: "OhMyGPT / 第三方 API 文生图（推荐分镜）",
}

_ALL_BACKENDS = (BACKEND_MINIMAX, BACKEND_SDXL, BACKEND_IMAGE_API)


def resolve_storyboard_backend(prefs: Optional[Dict[str, Any]] = None) -> str:
    if prefs:
        b = (prefs.get("storyboard_backend") or "").strip()
        if b in _ALL_BACKENDS:
            if b == BACKEND_IMAGE_API and not image_api_configured():
                return BACKEND_MINIMAX
            return b
    env = (os.environ.get("STORYBOARD_BACKEND") or BACKEND_IMAGE_API).strip()
    if env == BACKEND_IMAGE_API and not image_api_configured():
        return BACKEND_MINIMAX
    return env if env in _ALL_BACKENDS else BACKEND_IMAGE_API


def list_storyboard_backends() -> List[Dict[str, str]]:
    out = [
        {"id": BACKEND_MINIMAX, "label": BACKEND_LABELS[BACKEND_MINIMAX]},
        {"id": BACKEND_SDXL, "label": BACKEND_LABELS[BACKEND_SDXL]},
    ]
    if image_api_configured():
        out.append({"id": BACKEND_IMAGE_API, "label": BACKEND_LABELS[BACKEND_IMAGE_API]})
    else:
        out.append(
            {
                "id": BACKEND_IMAGE_API,
                "label": BACKEND_LABELS[BACKEND_IMAGE_API]
                + "（需在服务器配置 STORYBOARD_IMAGE_API_*）",
            }
        )
    return out


def sdxl_checkpoint_name() -> str:
    return (
        os.environ.get("STORYBOARD_SDXL_CKPT")
        or os.environ.get("STORYBOARD_CKPT")
        or "realisticVisionV60B1_v51VAE.safetensors"
    )


def build_sdxl_t2i_still_workflow(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    *,
    noise_seed: int = 42,
    steps: int = 28,
    cfg: float = 7.0,
    ckpt_name: Optional[str] = None,
    filename_prefix: str = "bingjing/shot_sdxl",
) -> Dict[str, Any]:
    """标准 SDXL/SD1.5 Checkpoint 文生图 → SaveImage（竖屏分镜推荐）。"""
    ckpt = ckpt_name or sdxl_checkpoint_name()
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative or "", "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": noise_seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": os.environ.get("STORYBOARD_SDXL_SAMPLER", "euler"),
                "scheduler": os.environ.get("STORYBOARD_SDXL_SCHEDULER", "normal"),
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": filename_prefix},
        },
    }


def build_single_shot_still_workflow(
    prompt: str,
    width: int,
    height: int,
    *,
    negative: str = "",
    noise_seed: int = 42,
    steps: int = 12,
    filename_prefix: str = "bingjing/shot",
    backend: Optional[str] = None,
    prefs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    b = backend or resolve_storyboard_backend(prefs)
    if b == BACKEND_SDXL:
        sdxl_steps = int(os.environ.get("STORYBOARD_SDXL_STEPS", str(max(steps, 25))))
        return build_sdxl_t2i_still_workflow(
            prompt,
            negative,
            width,
            height,
            noise_seed=noise_seed,
            steps=sdxl_steps,
            filename_prefix=filename_prefix,
        )
    return _build_minimax_single(
        prompt,
        width,
        height,
        noise_seed=noise_seed,
        steps=steps,
        filename_prefix=filename_prefix,
    )


def build_storyboard_workflow(
    prompt: str,
    width: int,
    height: int,
    *,
    noise_seed: int = 42,
    steps: int = 12,
    filename_prefix: str = "bingjing/storyboard",
    backend: Optional[str] = None,
    prefs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """整集拼图：仅 minimax_h3 支持 Drama10Storyboard；SDXL 请走逐镜生成。"""
    b = backend or resolve_storyboard_backend(prefs)
    if b != BACKEND_MINIMAX:
        raise ValueError(
            "当前分镜后端为 SDXL 文生图，不支持一次生成整集拼图，将自动改为逐镜出图。"
        )
    return _build_minimax_collage(
        prompt,
        width,
        height,
        noise_seed=noise_seed,
        steps=steps,
        filename_prefix=filename_prefix,
    )


def backend_from_project_id(project_id: str, get_prefs) -> str:
    """get_prefs: callable(project_id) -> dict"""
    return resolve_storyboard_backend(parse_prefs(get_prefs(project_id)))
