"""Background-music worker (ACE-Step 1.5). Standalone on purpose: runs inside the ACE-Step venv (it pins
torch 2.10), so it may only import the stdlib, numpy and ACE-Step itself (never shorts_factory).

usage: python music_worker.py manifest.json results.json
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import wave

import numpy as np


def mock_track(task: dict) -> None:
    """A soft seed-dependent chord so tests and --mock runs get real, distinct audio files."""
    sr, secs = 22050, float(task["seconds"])
    t = np.arange(int(sr * secs), dtype=np.float32) / sr
    root = 55.0 * 2 ** ((int(task["seed"]) % 12) / 12)
    chord = sum(np.sin(2 * np.pi * root * r * t) for r in (1.0, 1.5, 2.0)) / 3
    fade = np.minimum(1.0, np.minimum(t, secs - t) / 1.5)
    a = (0.3 * chord * fade).astype(np.float32)
    with wave.open(task["out"], "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.repeat(a[:, None], 2, axis=1) * 32767.0).astype("<i2").tobytes())


class AceStep:
    def __init__(self, p: dict, device: str):
        root = p["acestep_dir"]
        os.environ.setdefault("ACESTEP_PROJECT_ROOT", root)  # checkpoints live in <acestep_dir>/checkpoints
        from acestep.handler import AceStepHandler
        from acestep.llm_inference import LLMHandler
        from loguru import logger

        # ACE-Step logs every prompt and audio token at DEBUG; keep only problems so progress stays readable.
        logger.remove()
        logger.add(sys.stderr, level=os.environ.get("ACESTEP_LOG_LEVEL", "WARNING"))
        self.p = p
        self.dit = AceStepHandler()
        msg, ok = self.dit.initialize_service(project_root=root, config_path=p["dit_model"], device=device)
        if not ok:
            raise RuntimeError(f"ACE-Step model failed to load: {msg}")
        # The 5Hz LM plans structure/metadata before the DiT renders audio. Optional: without it
        # generation still works, just plainer, so a failed LM load is a warning, not an error.
        self.lm = LLMHandler()
        self.use_lm = False
        if p.get("lm_model"):
            from acestep.model_downloader import ensure_lm_model

            ckpt = os.path.join(root, "checkpoints")
            try:
                ensure_lm_model(model_name=p["lm_model"], checkpoints_dir=ckpt)
                msg, self.use_lm = self.lm.initialize(checkpoint_dir=ckpt, lm_model_path=p["lm_model"],
                                                      backend=p.get("lm_backend", "vllm"), device=device)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
            if not self.use_lm:
                print(f"[music] LM planner unavailable, continuing without it: {msg}", flush=True)
        self.tmp = tempfile.mkdtemp(prefix="acestep-")

    def generate(self, task: dict) -> None:
        from acestep.inference import GenerationConfig, GenerationParams, generate_music

        params = GenerationParams(
            task_type="text2music", caption=task["caption"], lyrics="[Instrumental]", instrumental=True,
            bpm=task.get("bpm"), duration=float(task["seconds"]),
            inference_steps=int(self.p.get("inference_steps", 8)), thinking=self.use_lm,
        )
        config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[int(task["seed"])],
                                  audio_format=os.path.splitext(task["out"])[1].lstrip(".") or "flac")
        res = generate_music(self.dit, self.lm, params, config, save_dir=self.tmp)
        if not res.success or not res.audios:
            raise RuntimeError(res.error or res.status_message or "no audio returned")
        audio = res.audios[0]
        if audio.get("path") and os.path.exists(audio["path"]):
            shutil.move(audio["path"], task["out"])
        else:
            import soundfile as sf

            sf.write(task["out"], audio["tensor"].numpy().T, int(audio["sample_rate"]))


def main(manifest_path: str, out_path: str) -> int:
    m = json.load(open(manifest_path))
    gen = AceStep(m.get("params", {}), m.get("device", "cuda")) if m["backend"] == "acestep" else None
    results: dict = {}
    total = len(m["tasks"])
    for n, task in enumerate(m["tasks"], 1):
        t0 = time.time()
        try:
            if gen is None:
                mock_track(task)
            else:
                gen.generate(task)
            results[task["id"]] = {"ok": True, "seconds": round(time.time() - t0, 2)}
        except Exception as e:
            traceback.print_exc()
            results[task["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        print(f"[music] {n}/{total} {task['id']}: {results[task['id']]}", flush=True)
    json.dump({"results": results}, open(out_path, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
