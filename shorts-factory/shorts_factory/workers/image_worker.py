"""Text-to-image worker (diffusers). Any DiffusionPipeline-compatible model id works; the pipeline
class is resolved from the model repo (Z-Image, FLUX, Qwen-Image, SDXL...).

usage: python image_worker.py manifest.json results.json
"""
from __future__ import annotations

import inspect
import json
import sys
import time
import traceback


def mock_image(task: dict, w: int, h: int) -> None:
    import numpy as np
    from PIL import Image, ImageDraw

    seed = int(task.get("seed", 0))
    top = np.array([(seed * 37) % 200 + 30, (seed * 91) % 160 + 40, (seed * 53) % 200 + 40], np.float32)
    bottom = top[[2, 0, 1]] / 3
    k = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    grad = (top * (1 - k) + bottom * k).astype(np.uint8)
    img = Image.fromarray(np.repeat(grad, w, axis=1))
    d = ImageDraw.Draw(img)
    for i in range(0, w, 96):  # texture so camera motion is visible in test renders
        d.line([(i, 0), (i + h // 3, h)], fill=(255, 255, 255), width=2)
    d.ellipse([w * 0.25, h * 0.2, w * 0.75, h * 0.2 + w * 0.5], outline=(255, 230, 120), width=10)
    d.text((40, 40), task["id"], fill=(255, 255, 255))
    img.save(task["out"], quality=95)


def main(manifest_path: str, out_path: str) -> int:
    m = json.load(open(manifest_path))
    p = m.get("params", {})
    w, h = int(p.get("width", 1088)), int(p.get("height", 1920))
    results: dict = {}
    pipe = None
    if m["backend"] == "diffusers":
        import torch
        from diffusers import DiffusionPipeline

        pipe = DiffusionPipeline.from_pretrained(p["model"], torch_dtype=torch.bfloat16)
        if p.get("cpu_offload"):
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(m.get("device", "cuda"))
        pipe.set_progress_bar_config(disable=True)
        accepted = inspect.signature(pipe.__call__).parameters
    for task in m["tasks"]:
        t0 = time.time()
        try:
            if pipe is None:
                mock_image(task, w, h)
            else:
                import torch

                kwargs = {
                    "prompt": task["prompt"], "width": w, "height": h,
                    "num_inference_steps": int(p.get("steps", 28)),
                    "guidance_scale": float(p.get("guidance_scale", 3.5)),
                    "generator": torch.Generator(device="cpu").manual_seed(int(task.get("seed", 0))),
                }
                neg = task.get("negative")
                uses_cfg = float(p.get("guidance_scale", 0)) > 1.0 or "true_cfg_scale" in p.get("extra", {})
                if neg and uses_cfg and "negative_prompt" in accepted:
                    kwargs["negative_prompt"] = neg
                kwargs.update(p.get("extra", {}))
                kwargs = {k: v for k, v in kwargs.items() if k in accepted}
                with torch.inference_mode():
                    image = pipe(**kwargs).images[0]
                image.save(task["out"], quality=95)
            results[task["id"]] = {"ok": True, "seconds": round(time.time() - t0, 2)}
        except Exception as e:
            traceback.print_exc()
            results[task["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        print(f"[image] {task['id']}: {results[task['id']]}", flush=True)
    json.dump({"results": results}, open(out_path, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
