"""Image-to-video worker (Wan 2.x via diffusers) for the hook scene(s).

usage: python animate_worker.py manifest.json results.json
"""
from __future__ import annotations

import json
import math
import sys
import time
import traceback

NEGATIVE = ("static, frozen, blurry, low quality, jpeg artifacts, flicker, jitter, distorted, deformed, "
            "morphing faces, extra limbs, text, subtitles, watermark, logo")


def main(manifest_path: str, out_path: str) -> int:
    m = json.load(open(manifest_path))
    p = m.get("params", {})
    results: dict = {}
    import torch
    from diffusers import AutoencoderKLWan, DiffusionPipeline
    from diffusers.utils import export_to_video, load_image

    vae = AutoencoderKLWan.from_pretrained(p["model"], subfolder="vae", torch_dtype=torch.float32)
    pipe = DiffusionPipeline.from_pretrained(p["model"], vae=vae, torch_dtype=torch.bfloat16)
    if p.get("cpu_offload"):
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(m.get("device", "cuda"))
    pipe.set_progress_bar_config(disable=True)
    mod = pipe.vae_scale_factor_spatial * pipe.transformer.config.patch_size[1]
    for task in m["tasks"]:
        t0 = time.time()
        try:
            image = load_image(task["image"])
            ar = image.height / image.width
            max_area = int(p.get("max_area", 480 * 832))
            h = round(math.sqrt(max_area * ar)) // mod * mod
            w = round(math.sqrt(max_area / ar)) // mod * mod
            image = image.resize((w, h))
            with torch.inference_mode():
                frames = pipe(
                    image=image, prompt=task["prompt"], negative_prompt=NEGATIVE, height=h, width=w,
                    num_frames=int(p.get("num_frames", 81)), num_inference_steps=int(p.get("steps", 40)),
                    guidance_scale=float(p.get("guidance_scale", 3.5)),
                    generator=torch.Generator(device="cpu").manual_seed(int(task.get("seed", 0))),
                ).frames[0]
            export_to_video(frames, task["out"], fps=int(p.get("fps", 16)))
            results[task["id"]] = {"ok": True, "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            traceback.print_exc()
            results[task["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        print(f"[animate] {task['id']}: {results[task['id']]}", flush=True)
    json.dump({"results": results}, open(out_path, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
