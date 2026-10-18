# -*- coding: utf-8 -*-
"""ComfyUI API 图：Drama10 分镜 / 成片（与 drama10_ice_realm_ep1_H3.json 对齐）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .comfy_client import CLIP, LORA, UNET, VAE_A, VAE_V


def _loader_nodes() -> Dict[str, Any]:
    return {
        "2": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET, "weight_dtype": "default"},
        },
        "3": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"},
        },
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_V}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_A}},
        "6": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["2", 0], "lora_name": LORA, "strength_model": 1.0},
        },
    }


def build_storyboard_workflow(
    prompt: str,
    width: int,
    height: int,
    *,
    noise_seed: int = 42,
    steps: int = 12,
    filename_prefix: str = "bingjing/storyboard",
) -> Dict[str, Any]:
    """Drama10StoryboardMiniMaxH3 → SaveImage（拼图）。"""
    g = _loader_nodes()
    g["10"] = {
        "class_type": "Drama10StoryboardMiniMaxH3",
        "inputs": {
            "model": ["6", 0],
            "clip": ["3", 0],
            "vae": ["4", 0],
            "prompt": prompt,
            "width": width,
            "height": height,
            "noise_seed": noise_seed,
            "steps": steps,
        },
    }
    g["11"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["10", 0], "filename_prefix": filename_prefix},
    }
    return g


def build_single_shot_still_workflow(
    prompt: str,
    width: int,
    height: int,
    *,
    noise_seed: int = 42,
    steps: int = 12,
    filename_prefix: str = "bingjing/shot",
    length: int = 13,
) -> Dict[str, Any]:
    """单镜静帧：MiniMax H3 短采样取末帧，不经过 Drama10Storyboard（避免误跑 10 镜）。"""
    g = _loader_nodes()
    g["7"] = {
        "class_type": "MiniMaxH3ImageToVideo",
        "inputs": {
            "clip": ["3", 0],
            "vae": ["4", 0],
            "prompt": prompt,
            "width": width,
            "height": height,
            "length": length,
        },
    }
    g["8"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": noise_seed}}
    g["9"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}}
    g["10"] = {
        "class_type": "BasicScheduler",
        "inputs": {"model": ["6", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0},
    }
    g["11"] = {
        "class_type": "BasicGuider",
        "inputs": {"model": ["6", 0], "conditioning": ["7", 0]},
    }
    g["12"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["8", 0],
            "guider": ["11", 0],
            "sampler": ["9", 0],
            "sigmas": ["10", 0],
            "latent_image": ["7", 1],
        },
    }
    g["13"] = {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}}
    g["14"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["13", 0], "filename_prefix": filename_prefix},
    }
    return g


def build_episode_video_workflow(
    prompt: str,
    width: int,
    height: int,
    *,
    shot_duration: float = 9.0,
    noise_seed: int = 42,
    steps: int = 7,
    grid_cols: int = 5,
    grid_rows: int = 2,
    auto_storyboard: bool = True,
    resume: bool = False,
    collage_image: Optional[str] = None,
    shot_stills_subdir: Optional[str] = None,
    ref_images_subdir: Optional[str] = None,
    video_prefix: str = "bingjing/ep_video",
    storyboard_prefix: str = "bingjing/ep_storyboard",
    single_shot_index: int = 0,
    finalize_mux: bool = True,
    preview_with_audio: bool = True,
    turbo_mode: bool = False,
    shift_video: float = 12.0,
    shift_audio: float = 3.0,
) -> Dict[str, Any]:
    """Drama10ShotMiniMaxH3 → SaveVideo + SaveImage（成片 + 分镜拼图）。"""
    g = _loader_nodes()
    # 模型使用标准：video sigma shift=12 / audio sigma shift=3
    g["6b"] = {
        "class_type": "MiniMaxH3SigmaShift",
        "inputs": {
            "model": ["6", 0],
            "shift_video": float(shift_video),
            "shift_audio": float(shift_audio),
        },
    }
    drama_inputs: Dict[str, Any] = {
        "model": ["6b", 0],
        "clip": ["3", 0],
        "vae": ["4", 0],
        "audio_vae": ["5", 0],
        "prompt": prompt,
        "width": width,
        "height": height,
        # 单镜 3~10s（内容少可短）
        "shot_duration": min(10.0, max(3.0, float(shot_duration))),
        "noise_seed": noise_seed,
        "turbo_mode": bool(turbo_mode),
        "steps": int(max(6, min(7, steps))),
        # Comfy 节点 Drama10ShotMiniMaxH3：grid_cols / grid_rows 均最大 10
        "grid_cols": max(1, min(10, int(grid_cols or 5))),
        "grid_rows": max(1, min(10, int(grid_rows or 2))),
        "auto_storyboard": auto_storyboard,
        "resume": resume,
        "single_shot_index": int(single_shot_index),
        "finalize_mux": bool(finalize_mux),
        "preview_with_audio": bool(preview_with_audio),
    }
    if collage_image:
        g["7"] = {"class_type": "LoadImage", "inputs": {"image": collage_image}}
        drama_inputs["first_frame"] = ["7", 0]
    subdir = (shot_stills_subdir or "").strip().replace("\\", "/")
    drama_inputs["shot_stills_subdir"] = subdir
    if subdir:
        drama_inputs["auto_storyboard"] = False
    ref_sub = (ref_images_subdir or "").strip().replace("\\", "/")
    if ref_sub:
        drama_inputs["ref_images_subdir"] = ref_sub
    g["20"] = {"class_type": "Drama10ShotMiniMaxH3", "inputs": drama_inputs}
    g["21"] = {
        "class_type": "SaveVideo",
        "inputs": {
            "video": ["20", 0],
            "filename_prefix": video_prefix,
            "format": "auto",
            "codec": "auto",
        },
    }
    g["22"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["20", 1], "filename_prefix": storyboard_prefix},
    }
    return g
