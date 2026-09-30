#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
10镜 MiniMax H3 短剧全流程：逐镜生成 → ffmpeg 自动拼接
用法:
  cd /home/linux && python3 run_drama_pipeline.py
  python3 run_drama_pipeline.py --shots 1,2,3   # 只跑部分镜
  python3 run_drama_pipeline.py --no-concat     # 不拼接
"""
from __future__ import annotations
import argparse, json, os, time, uuid, subprocess, urllib.request, urllib.error, http.cookiejar
from pathlib import Path

COMFY = Path("/home/linux/ComfyUI")
INPUT = COMFY / "input"
OUTPUT = COMFY / "output" / "drama"
SCRIPTS = COMFY / "input" / "drama_shots" / "scripts.json"
PASSWORD = COMFY / "login" / "PASSWORD"
BASE = "http://127.0.0.1:8000"

# 3090 稳妥参数（约 0.2MP / 6秒）
WIDTH, HEIGHT = 352, 608  # 竖屏分镜
DURATION_SEC = 6
LENGTH = max(5, round(DURATION_SEC * 24))
LENGTH = LENGTH + (5 - (LENGTH % 17)) % 17  # snap 17k+5
STEPS = 8
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE_V = "minimax_h3_video_vae_fp16.safetensors"
VAE_A = "minimax_h3_audio_vae_fp32.safetensors"
LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"


def auth_headers():
    token = PASSWORD.read_bytes().split(b"\n", 1)[0].decode("utf-8", "replace").strip()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def http_json(method, path, data=None, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=None if data is None else json.dumps(data).encode("utf-8"),
        headers=auth_headers(),
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def build_prompt(image_rel: str, text: str, seed: int, prefix: str):
    # API graph for one MiniMax H3 I2V shot (turbo 8step)
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_rel}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_V}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_A}},
        "6": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["2", 0], "lora_name": LORA, "strength_model": 1.0},
        },
        "7": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["3", 0],
                "vae": ["4", 0],
                "prompt": text,
                "width": WIDTH,
                "height": HEIGHT,
                "length": LENGTH,
                "first_frame": ["1", 0],
            },
        },
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "10": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["6", 0], "scheduler": "simple", "steps": STEPS, "denoise": 1.0},
        },
        "11": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["6", 0], "conditioning": ["7", 0]},
        },
        "12": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["8", 0],
                "guider": ["11", 0],
                "sampler": ["9", 0],
                "sigmas": ["10", 0],
                "latent_image": ["7", 1],
            },
        },
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["5", 0]}},
        "15": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["13", 0], "fps": 24.0, "audio": ["14", 0], "bit_depth": 8},
        },
        "16": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["15", 0],
                "filename_prefix": prefix,
                "format": "auto",
                "codec": "auto",
            },
        },
    }


def wait_done(prompt_id: str, timeout=3600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hist = http_json("GET", f"/history/{prompt_id}")
        if prompt_id in hist:
            status = hist[prompt_id].get("status", {})
            if status.get("completed") or hist[prompt_id].get("outputs"):
                if status.get("status_str") == "error":
                    raise RuntimeError(json.dumps(status, ensure_ascii=False)[:2000])
                return hist[prompt_id]
        # also check queue
        q = http_json("GET", "/queue")
        running = q.get("queue_running") or []
        pending = q.get("queue_pending") or []
        print(f"  … waiting {int(time.time()-t0)}s  running={len(running)} pending={len(pending)}", flush=True)
        time.sleep(8)
    raise TimeoutError(prompt_id)


def find_output_video(prefix: str) -> Path:
    # SaveVideo writes under output/; search newest matching
    candidates = sorted(OUTPUT.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    # also default output root
    candidates += sorted((COMFY / "output").rglob(f"*{Path(prefix).name}*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    # unique
    seen, uniq = set(), []
    for p in candidates:
        if p.resolve() not in seen:
            seen.add(p.resolve())
            uniq.append(p)
    # prefer name containing prefix basename
    base = Path(prefix).name
    for p in uniq:
        if base in p.name:
            return p
    if uniq:
        return uniq[0]
    raise FileNotFoundError(prefix)


def concat_ffmpeg(clips: list[Path], out: Path):
    lst = out.parent / "concat_list.txt"
    with lst.open("w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c.resolve()}'\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", str(out),
    ]
    print("CONCAT", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)
    print("FINAL", out, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="", help="例如 1,2,3 只跑这些镜；空=全部")
    ap.add_argument("--no-concat", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    data = json.loads(SCRIPTS.read_text(encoding="utf-8"))
    shots = data["shots"]
    if args.shots.strip():
        want = {int(x) for x in args.shots.split(",") if x.strip()}
        shots = [s for s in shots if s["id"] in want]

    OUTPUT.mkdir(parents=True, exist_ok=True)
    # ping
    try:
        http_json("GET", "/system_stats")
    except Exception as e:
        raise SystemExit(f"无法连接 ComfyUI {BASE}: {e}")

    clips = []
    for s in shots:
        sid = s["id"]
        img = s["image"]
        if not (INPUT / img).exists():
            raise FileNotFoundError(INPUT / img)
        prefix = f"drama/shot_{sid:02d}"
        seed = args.seed + sid
        print(f"\n===== SHOT {sid:02d}  {img}  {WIDTH}x{HEIGHT} len={LENGTH} =====", flush=True)
        prompt = build_prompt(img, s["prompt"], seed, prefix)
        client_id = str(uuid.uuid4())
        resp = http_json("POST", "/prompt", {"prompt": prompt, "client_id": client_id})
        pid = resp["prompt_id"]
        print("queued", pid, flush=True)
        hist = wait_done(pid)
        # locate file from outputs
        outs = hist.get("outputs") or {}
        video_path = None
        for node_out in outs.values():
            for v in node_out.get("images", []) + node_out.get("gifs", []) + node_out.get("videos", []):
                # SaveVideo may use videos key
                pass
            for v in node_out.get("videos", []) or []:
                fp = COMFY / "output" / v.get("subfolder", "") / v["filename"]
                if fp.exists():
                    video_path = fp
        if video_path is None:
            # fallback search
            time.sleep(1)
            video_path = find_output_video(prefix)
        dest = OUTPUT / f"shot_{sid:02d}.mp4"
        if video_path.resolve() != dest.resolve():
            dest.write_bytes(video_path.read_bytes())
        print("saved", dest, flush=True)
        clips.append(dest)

    if not args.no_concat and clips:
        final = OUTPUT / "drama_full_10shots.mp4"
        # only concat all 10 if we have them; else concat what we ran
        all_clips = [OUTPUT / f"shot_{i:02d}.mp4" for i in range(1, 11)]
        if all(p.exists() for p in all_clips):
            concat_ffmpeg(all_clips, final)
        else:
            concat_ffmpeg(clips, OUTPUT / "drama_partial.mp4")
    print("\nALL DONE", flush=True)


if __name__ == "__main__":
    main()
