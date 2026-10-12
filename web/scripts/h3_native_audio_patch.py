# -*- coding: utf-8 -*-
"""Run ON H3: patch drama10_nodes.py for MiniMax native AV, no TTS, no burned subs."""
from __future__ import annotations

import ast
import re
import shutil
import time
from pathlib import Path

PATH = Path("/home/linux/ComfyUI/custom_nodes/ComfyUI_Drama10/drama10_nodes.py")


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    bak = PATH.with_suffix(PATH.suffix + f".bak_native_{int(time.time())}")
    shutil.copy2(PATH, bak)
    print("BACKUP", bak)

    text2, n = re.subn(
        r"I2V_NO_TEXT_SUFFIX\s*=\s*\([\s\S]*?\)\n",
        (
            "I2V_NO_TEXT_SUFFIX = (\n"
            '    "严禁画面出现任何字幕/汉字/字母/标题/水印/Logo/UI/弹幕/台词条；"\n'
            '    "绝不烧录字幕；台词只在音轨，画面上不要显示任何文字。"\n'
            ")\n"
        ),
        text,
        count=1,
    )
    text = text2
    print("I2V_NO_TEXT_SUFFIX", n)

    new_gen = '''def _gen_one_shot(
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
    audio_vae=None,
):
    """采样 MiniMax H3 联合 AV latent：视频 VAE + 音频 VAE 原生解码（不做 TTS）。"""
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
    print("[Drama10] VAE decode video (+native audio if audio_vae)…", flush=True)
    images = nodes.VAEDecode().decode(vae, out_lat)[0]
    audio = None
    if audio_vae is not None:
        try:
            from comfy_extras.nodes_audio import vae_decode_audio

            audio = vae_decode_audio(audio_vae, out_lat)
            print("[Drama10] MiniMax native audio decoded", flush=True)
        except Exception as e:
            print(f"[Drama10] native audio decode fail (no TTS fallback): {e}", flush=True)
            audio = None
    del out_lat
    gc.collect()
    comfy.model_management.soft_empty_cache()
    return _as_thwc(images).detach().cpu().half().contiguous(), audio
'''

    m = re.search(
        r"def _gen_one_shot\([\s\S]*?\n    return _as_thwc\(images\)\.detach\(\)\.cpu\(\)\.half\(\)\.contiguous\(\)\n",
        text,
    )
    if not m:
        raise SystemExit("FAIL: cannot find _gen_one_shot")
    text = text[: m.start()] + new_gen + text[m.end() :]
    print("patched _gen_one_shot")

    text2, n = re.subn(
        r"imgs = _gen_one_shot\(\n                model, clip, vae, p, width, height, length, None, int\(noise_seed\) \+ i, steps, sampler\n            \)",
        "imgs, _ = _gen_one_shot(\n"
        "                model, clip, vae, p, width, height, length, None, int(noise_seed) + i, steps, sampler\n"
        "            )",
        text,
        count=1,
    )
    if not n:
        text2, n = re.subn(
            r"imgs = _gen_one_shot\(\s*model, clip, vae, p, width, height, length, None, int\(noise_seed\) \+ i, steps, sampler\s*\)",
            "imgs, _ = _gen_one_shot(model, clip, vae, p, width, height, length, None, int(noise_seed) + i, steps, sampler)",
            text,
            count=1,
        )
    text = text2
    print("storyboard caller", n)

    helper = '''
def _attach_native_audio_to_mp4(mp4_path: Path, audio, wav_path: Path) -> bool:
    """把 MiniMax 原生音频 mux 进已编码的无声 mp4；绝不烧字幕、不做 TTS。"""
    if not audio or not isinstance(audio, dict) or "waveform" not in audio:
        return False
    try:
        _write_wav(audio, wav_path)
        out_av = mp4_path.with_suffix(".av.mp4")
        _mux_av(mp4_path, wav_path, out_av, None)
        if out_av.is_file() and out_av.stat().st_size > 512:
            mp4_path.write_bytes(out_av.read_bytes())
            out_av.unlink(missing_ok=True)
            ok = _mp4_has_audio_stream(mp4_path)
            print(f"[Drama10] native AV mux -> {mp4_path.name} audio={ok}", flush=True)
            return ok
    except Exception as e:
        print(f"[Drama10] native AV mux fail: {e}", flush=True)
    return False

'''
    if "_attach_native_audio_to_mp4" not in text:
        text = text.replace(
            "def _shot_mp4_path(ckpt: Path, shot_num: int) -> Path:",
            helper + "def _shot_mp4_path(ckpt: Path, shot_num: int) -> Path:",
            1,
        )
        print("inserted helper")

    old_call = re.search(
        r"imgs = _gen_one_shot\(\n"
        r"                    model,\n"
        r"                    clip,\n"
        r"                    vae,\n"
        r"                    i2v_prompt,\n"
        r"                    width,\n"
        r"                    height,\n"
        r"                    length_i,\n"
        r"                    tile_in,\n"
        r"                    int\(noise_seed\) \+ orig_sn - 1,\n"
        r"                    i2v_steps,\n"
        r"                    sampler,\n"
        r"                \)\n"
        r"                imgs = _correct_i2v_orientation\(\n"
        r"                    tile_in, imgs, int\(width\), int\(height\), orig_sn=orig_sn\n"
        r"                \)\n"
        r"                if imgs\.dtype != torch\.float16:\n"
        r"                    imgs = _as_thwc\(imgs\)\.detach\(\)\.cpu\(\)\.half\(\)\.contiguous\(\)\n"
        r"                _save_shot_pt\(sp, imgs\)\n"
        r"                _release_vram_after_sampling\(\)\n"
        r"                _encode_shot_mp4\(sp, mp4, float\(FPS\)\)\n",
        text,
    )
    if not old_call:
        raise SystemExit("FAIL: cannot find i2v call block")
    new_call = '''imgs, native_audio = _gen_one_shot(
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
                    audio_vae=audio_vae,
                )
                imgs = _correct_i2v_orientation(
                    tile_in, imgs, int(width), int(height), orig_sn=orig_sn
                )
                if imgs.dtype != torch.float16:
                    imgs = _as_thwc(imgs).detach().cpu().half().contiguous()
                _save_shot_pt(sp, imgs)
                _release_vram_after_sampling()
                _encode_shot_mp4(sp, mp4, float(FPS))
                wav_native = ckpt / "mp4" / f"shot_{orig_sn:02d}.native.wav"
                wav_native.parent.mkdir(exist_ok=True)
                if not _attach_native_audio_to_mp4(mp4, native_audio, wav_native):
                    print(
                        f"[Drama10] shot {orig_sn}: no MiniMax native audio after encode; "
                        "will NOT TTS-remux (check script / storyboard)",
                        flush=True,
                    )
'''
    text = text[: old_call.start()] + new_call + text[old_call.end() :]
    print("patched i2v block")

    old_mux = re.search(
        r"            if want_preview_audio:\n"
        r"                if _mp4_has_audio_stream\(mp4\):\n"
        r"                    print\(\n"
        r"                        f\"\[Drama10\] single-shot \{orig_sn\} mp4 already has audio, skip remux\",\n"
        r"                        flush=True,\n"
        r"                    \)\n"
        r"                else:\n"
        r"                    try:\n"
        r"                        comfy\.model_management\.unload_all_models\(\)\n"
        r"                    except Exception:\n"
        r"                        pass\n"
        r"                    comfy\.model_management\.soft_empty_cache\(\)\n"
        r"                    gc\.collect\(\)\n"
        r"                    i_ep = \(\n"
        r"                        orig_shot_nums\.index\(orig_sn\)\n"
        r"                        if orig_sn in orig_shot_nums\n"
        r"                        else max\(0, orig_sn - 1\)\n"
        r"                    \)\n"
        r"                    vl = voice_per_shot\[i_ep\] if i_ep < len\(voice_per_shot\) else \[\]\n"
        r"                    amb = ambience_per_shot\[i_ep\] if i_ep < len\(ambience_per_shot\) else \"\"\n"
        r"                    print\(\n"
        r"                        f\"\[Drama10\] mux shot\{orig_sn\} voices=\{len\(vl\)\} amb=\{bool\(amb\)\}\",\n"
        r"                        flush=True,\n"
        r"                    \)\n"
        r"                    _mux_one_shot_preview_audio\(\n"
        r"                        ckpt,\n"
        r"                        mp4,\n"
        r"                        vl,\n"
        r"                        amb,\n"
        r"                        int\(noise_seed\),\n"
        r"                    \)\n",
        text,
    )
    if not old_mux:
        raise SystemExit("FAIL: cannot find TTS remux block")
    new_mux = '''            # 禁止 TTS/BGM 混音：只保留 MiniMax 原生音轨
            if _mp4_has_audio_stream(mp4):
                print(
                    f"[Drama10] single-shot {orig_sn} MiniMax native audio OK",
                    flush=True,
                )
            else:
                print(
                    f"[Drama10] single-shot {orig_sn} missing native audio; "
                    "TTS remux DISABLED — fix script/storyboard and rerun",
                    flush=True,
                )
'''
    text = text[: old_mux.start()] + new_mux + text[old_mux.end() :]
    print("disabled TTS remux")

    old_final = re.search(
        r"        speech, sub_events = _build_speech_timeline\(\n"
        r"            voice_per_shot,\n"
        r"            shot_starts,\n"
        r"            actual_durs,\n"
        r"            episode_shot_indices=list\(range\(len\(voice_per_shot\)\)\),\n"
        r"            episode_wide_sync=True,\n"
        r"        \)\n"
        r"        bgm = _continuous_bgm\(max\(dur, acc\) \+ 2\.5, seed=int\(noise_seed\)\)\n"
        r"        sfx = _build_ambience_timeline\(\n"
        r"            ambience_per_shot, actual_durs, seed=int\(noise_seed\) \+ 17\n"
        r"        \)\n"
        r"        audio = _mix_audio\(speech, bgm, sfx\)\n"
        r"        del speech, bgm, sfx\n"
        r"\n"
        r"        need = int\(dur \* SR\)\n"
        r"        w = audio\[\"waveform\"\]\n"
        r"        if w\.dim\(\) == 2:\n"
        r"            w = w\.unsqueeze\(0\)\n"
        r"        if w\.shape\[-1\] > need:\n"
        r"            w = w\[:, :, :need\]\n"
        r"        elif w\.shape\[-1\] < need:\n"
        r"            w = torch\.cat\(\[w, torch\.zeros\(1, w\.shape\[1\], need - w\.shape\[-1\]\)\], dim=-1\)\n"
        r"        audio = \{\"waveform\": w, \"sample_rate\": SR\}\n"
        r"        wav_path = ckpt / \"audio\.wav\"\n"
        r"        _write_wav\(audio, wav_path\)\n"
        r"\n"
        r"        srt_path = ckpt / \"subs\.srt\"\n"
        r"        _write_srt\(sub_events, srt_path\)\n"
        r"        burn_subs = os\.environ\.get\(\"DRAMA10_BURN_SUBS\", \"0\"\)\.strip\(\)\.lower\(\) in \(\n"
        r"            \"1\",\n"
        r"            \"true\",\n"
        r"            \"yes\",\n"
        r"            \"on\",\n"
        r"        \)\n"
        r"\n"
        r"        final_mp4 = Path\(folder_paths\.get_output_directory\(\)\) / \"drama_ui\" / f\"ice_realm_ep1_\{job\}\.mp4\"\n"
        r"        final_mp4\.parent\.mkdir\(parents=True, exist_ok=True\)\n"
        r"        _mux_av\(silent, wav_path, final_mp4, srt_path if burn_subs else None\)\n",
        text,
    )
    if not old_final:
        raise SystemExit("FAIL: cannot find finalize block")
    new_final = '''        final_mp4 = Path(folder_paths.get_output_directory()) / "drama_ui" / f"ice_realm_ep1_{job}.mp4"
        final_mp4.parent.mkdir(parents=True, exist_ok=True)
        # 禁止整集 TTS/BGM/烧字幕：直接使用各镜 MiniMax 原生音轨拼接结果
        import shutil as _shutil
        _shutil.copyfile(silent, final_mp4)
        n_av = sum(1 for p in shot_mp4s if _mp4_has_audio_stream(p))
        print(
            f"[Drama10] final concat only (no TTS/subs); "
            f"native-audio shots={n_av}/{len(shot_mp4s)} -> {final_mp4}",
            flush=True,
        )
'''
    text = text[: old_final.start()] + new_final + text[old_final.end() :]
    print("patched finalize")

    text2, n = re.subn(
        r"def _mux_av\(\n"
        r"    video_mp4: Path,\n"
        r"    audio_wav: Path,\n"
        r"    out_mp4: Path,\n"
        r"    srt_path: Optional\[Path\] = None,\n"
        r"\):\n"
        r"    if srt_path and srt_path\.is_file\(\) and srt_path\.stat\(\)\.st_size > 8:\n",
        (
            "def _mux_av(\n"
            "    video_mp4: Path,\n"
            "    audio_wav: Path,\n"
            "    out_mp4: Path,\n"
            "    srt_path: Optional[Path] = None,\n"
            "):\n"
            "    # 产品要求：画面永不烧录字幕\n"
            "    srt_path = None\n"
            "    if False and srt_path and srt_path.is_file() and srt_path.stat().st_size > 8:\n"
        ),
        text,
        count=1,
    )
    text = text2
    print("no burn-in", n)

    text = text.replace(
        'print("[Drama10] single-shot preview: will mux audio after i2v", flush=True)',
        'print("[Drama10] single-shot: MiniMax native AV (no TTS remux)", flush=True)',
    )

    PATH.write_text(text, encoding="utf-8")
    ast.parse(PATH.read_text(encoding="utf-8"))
    print("SYNTAX_OK")


if __name__ == "__main__":
    main()
