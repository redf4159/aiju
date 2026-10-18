# -*- coding: utf-8 -*-
"""LTX-2.5 INT8 图生视频（原生音频）ComfyUI API 图。"""
from __future__ import annotations

from typing import Any, Dict, Optional

# INT8 + 可变时长 3~10s + CFG~3 + modality_scale=3
LTX_UNET = "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
LTX_CLIP = "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
# 官方蓝图默认非 conv；若本机有 conv 版可在调用时覆盖
LTX_VAE_V = "ltx-2.5-video-vae-bf16.safetensors"
LTX_VAE_V_FALLBACK = "ltx-2.5-video-vae-conv-bf16.safetensors"
LTX_VAE_A = "ltx-2.5-audio-vae-bf16.safetensors"

# HF 下载清单（INT8 蒸馏，适合 24GB）
LTX_HF_REPO = "Lightricks/LTX-2.5"
LTX_HF_FILES = (
    ("diffusion_models", LTX_UNET),
    ("text_encoders", LTX_CLIP),
    ("vae", LTX_VAE_V),
    ("vae", LTX_VAE_A),
)

# Distilled 官方模板常用固定 sigma；6~8 步
LTX_DISTILLED_SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"


def ltx_frame_length(duration_sec: float, fps: int = 24) -> int:
    """length = duration*fps + 1，且满足 8n+1。"""
    dur = max(3.0, min(10.0, float(duration_sec or 5.0)))
    raw = int(round(dur * int(fps))) + 1
    # 向下对齐到 8n+1
    n = max(1, (raw - 1) // 8)
    return int(n * 8 + 1)


def build_ltx25_i2v_workflow(
    *,
    image_name: str,
    positive: str,
    negative: str,
    width: int = 768,
    height: int = 1360,
    duration_sec: float = 9.0,
    fps: int = 24,
    noise_seed: int = 42,
    cfg: float = 3.2,
    modality_scale: float = 3.0,
    video_prefix: str = "bingjing/ltx25",
    vae_video_name: Optional[str] = None,
) -> Dict[str, Any]:
    """单镜 LTX-2.5 I2V + 原生音频（单阶段，INT8，适合 24GB）。"""
    w = max(64, int(width) // 32 * 32)
    h = max(64, int(height) // 32 * 32)
    length = ltx_frame_length(duration_sec, fps=fps)
    vae_v = (vae_video_name or LTX_VAE_V).strip() or LTX_VAE_V
    cfg_v = float(max(1.0, min(3.5, cfg)))
    mod = float(max(1.0, min(10.0, modality_scale)))

    g: Dict[str, Any] = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": LTX_UNET, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": LTX_CLIP, "type": "ltxv", "device": "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae_v}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_VAE_A}},
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": positive},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": negative},
        },
        "7": {
            "class_type": "LTXVConditioning",
            "inputs": {
                "positive": ["5", 0],
                "negative": ["6", 0],
                "frame_rate": float(fps),
            },
        },
        "8": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
        },
        "9": {
            "class_type": "LTXVPreprocess",
            "inputs": {"image": ["8", 0], "img_compression": 8},
        },
        # 先对纯视频 latent 做 I2V，再与音频 latent 拼接。
        # LTXVImgToVideoInplace 会对 samples.clone()，不能吃 Concat 后的 NestedTensor。
        "10": {
            "class_type": "EmptyLTXVLatentVideo",
            "inputs": {
                "width": w,
                "height": h,
                "length": length,
                "batch_size": 1,
            },
        },
        "11": {
            "class_type": "LTXVImgToVideoInplace",
            "inputs": {
                "vae": ["3", 0],
                "image": ["9", 0],
                "latent": ["10", 0],
                # strength=1 最大锁静帧，减轻跑画风/跳跃/道具放大
                "strength": 1.0,
                "bypass": False,
            },
        },
        "12": {
            "class_type": "LTXVEmptyLatentAudio",
            "inputs": {
                "frames_number": length,
                "frame_rate": float(fps),
                "batch_size": 1,
                "audio_vae": ["4", 0],
            },
        },
        "13": {
            "class_type": "LTXVConcatAVLatent",
            "inputs": {"video_latent": ["11", 0], "audio_latent": ["12", 0]},
        },
        "14": {
            "class_type": "LTXVModalityGuidance",
            "inputs": {
                "model": ["1", 0],
                "modality_scale": mod,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
        },
        "15": {
            "class_type": "LTXVDualCFGGuider",
            "inputs": {
                "model": ["14", 0],
                "positive": ["7", 0],
                "negative": ["7", 1],
                "video_cfg": cfg_v,
                "audio_cfg": cfg_v,
            },
        },
        "16": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler_ancestral"},
        },
        "17": {
            "class_type": "ManualSigmas",
            "inputs": {"sigmas": LTX_DISTILLED_SIGMAS},
        },
        "18": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": int(noise_seed)},
        },
        "19": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["18", 0],
                "guider": ["15", 0],
                "sampler": ["16", 0],
                "sigmas": ["17", 0],
                "latent_image": ["13", 0],
            },
        },
        "20": {
            "class_type": "LTXVSeparateAVLatent",
            "inputs": {"av_latent": ["19", 0]},
        },
        "21": {
            "class_type": "VAEDecodeTiled",
            "inputs": {
                "samples": ["20", 0],
                "vae": ["3", 0],
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 64,
                "temporal_overlap": 16,
            },
        },
        "22": {
            "class_type": "LTXVAudioVAEDecode",
            "inputs": {"samples": ["20", 1], "audio_vae": ["4", 0]},
        },
        "23": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["21", 0],
                "fps": float(fps),
                "audio": ["22", 0],
            },
        },
        "24": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["23", 0],
                "filename_prefix": video_prefix,
                "format": "auto",
                "codec": "auto",
            },
        },
    }
    return g
