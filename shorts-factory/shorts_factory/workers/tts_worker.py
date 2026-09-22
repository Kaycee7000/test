"""TTS worker. Standalone on purpose: runs inside the isolated Chatterbox venv, so it may only
import the stdlib, numpy and the TTS backend itself (never shorts_factory).

usage: python tts_worker.py manifest.json results.json
"""
from __future__ import annotations

import inspect
import json
import math
import os
import sys
import traceback
import urllib.request
import wave

import numpy as np

KOKORO_LANG = {"en": "a", "en-gb": "b", "es": "e", "fr": "f", "hi": "h", "it": "i", "pt": "p", "ja": "j", "zh": "z"}


def write_wav(path: str, audio: np.ndarray, sr: int) -> None:
    a = np.clip(audio.astype(np.float32), -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((a * 32767.0).astype("<i2").tobytes())


def trim_and_pad(audio: np.ndarray, sr: int, lead: float, tail: float, floor_db: float = -42.0) -> np.ndarray:
    """Cut model-generated leading/trailing silence and breaths, then add consistent padding.
    Tight, uniform gaps between scenes are a big part of the fast Shorts pacing."""
    win = max(1, int(sr * 0.01))
    n = len(audio) // win
    if n == 0:
        return audio
    frames = audio[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms / (np.max(rms) + 1e-9) + 1e-12)
    voiced = np.where(db > floor_db)[0]
    if len(voiced):
        a, b = max(0, voiced[0] - 2) * win, min(len(audio), (voiced[-1] + 4) * win)
        audio = audio[a:b]
    out = np.concatenate([np.zeros(int(sr * lead), np.float32), audio.astype(np.float32),
                          np.zeros(int(sr * tail), np.float32)])
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    return out * (0.95 / peak) if peak > 0.95 else out


class Backend:
    def __init__(self, m: dict):
        self.m = m
        self.params = m.get("params", {})
        self.device = m.get("device", "cuda")
        self._models: dict = {}

    def synth(self, task: dict, attempt: int) -> tuple[np.ndarray, int]:
        raise NotImplementedError


class Chatterbox(Backend):
    def _model(self, lang: str):
        variant = self.params.get("variant", "standard")
        key = (variant, lang == "en")
        if key not in self._models:
            if lang != "en":
                from chatterbox.mtl_tts import ChatterboxMultilingualTTS
                self._models[key] = ChatterboxMultilingualTTS.from_pretrained(device=self.device)
            elif variant == "turbo":
                from chatterbox.tts_turbo import ChatterboxTurboTTS
                self._models[key] = ChatterboxTurboTTS.from_pretrained(device=self.device)
            else:
                from chatterbox.tts import ChatterboxTTS
                self._models[key] = ChatterboxTTS.from_pretrained(device=self.device)
        return self._models[key]

    def synth(self, task: dict, attempt: int) -> tuple[np.ndarray, int]:
        import torch

        lang = task.get("language", "en")
        model = self._model(lang)
        torch.manual_seed(int(task.get("seed", 0)) + attempt * 7919)
        kwargs = {
            "exaggeration": task.get("exaggeration") or self.params.get("exaggeration"),
            "cfg_weight": self.params.get("cfg_weight"),
            "temperature": self.params.get("temperature"),
        }
        ref = task.get("voice_ref")
        if ref and os.path.exists(ref):
            kwargs["audio_prompt_path"] = ref
        elif self.params.get("variant") == "turbo":
            raise RuntimeError(f"chatterbox turbo needs a voice reference clip; missing {ref}")
        if lang != "en":
            kwargs["language_id"] = lang
        accepted = inspect.signature(model.generate).parameters
        kwargs = {k: v for k, v in kwargs.items() if v is not None and k in accepted}
        with torch.inference_mode():
            wav = model.generate(task["text"], **kwargs)
        return wav.squeeze().detach().float().cpu().numpy(), int(model.sr)


class Kokoro(Backend):
    def synth(self, task: dict, attempt: int) -> tuple[np.ndarray, int]:
        from kokoro import KPipeline

        code = KOKORO_LANG.get(task.get("language", "en"), "a")
        if code not in self._models:
            self._models[code] = KPipeline(lang_code=code, device=self.device)
        pipe = self._models[code]
        chunks = []
        for _, _, audio in pipe(task["text"], voice=task.get("kokoro_voice") or "am_michael",
                                speed=float(self.params.get("speed", 1.0))):
            if audio is not None:
                chunks.append(audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio))
        if not chunks:
            raise RuntimeError("kokoro produced no audio")
        return np.concatenate(chunks).astype(np.float32), 24000


class ElevenLabs(Backend):
    def synth(self, task: dict, attempt: int) -> tuple[np.ndarray, int]:
        key = os.environ.get("ELEVENLABS_API_KEY")
        voice = task.get("elevenlabs_voice_id")
        if not key or not voice:
            raise RuntimeError("ELEVENLABS_API_KEY and voice.elevenlabs_voice_id are required")
        body = {
            "text": task["text"],
            "model_id": self.params.get("model_id", "eleven_multilingual_v2"),
            "voice_settings": {k: self.params[k] for k in ("stability", "similarity_boost", "style") if k in self.params},
        }
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_24000",
            data=json.dumps(body).encode(), headers={"xi-api-key": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            pcm = r.read()
        return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0, 24000


class Mock(Backend):
    """Speech-like placeholder audio: syllable-rate amplitude envelope over a voiced tone."""

    def synth(self, task: dict, attempt: int) -> tuple[np.ndarray, int]:
        sr = 24000
        words = max(1, len(task["text"].split()))
        dur = words / 2.7
        t = np.arange(int(sr * dur)) / sr
        f0 = 120 + 20 * np.sin(2 * math.pi * 0.7 * t)
        tone = 0.5 * np.sin(2 * math.pi * f0 * t) + 0.2 * np.sin(4 * math.pi * f0 * t)
        env = np.clip(np.sin(2 * math.pi * 4.2 * t), 0, 1) ** 0.6
        return (0.3 * tone * env).astype(np.float32), sr


BACKENDS = {"chatterbox": Chatterbox, "kokoro": Kokoro, "elevenlabs": ElevenLabs, "mock": Mock}


def main(manifest_path: str, out_path: str) -> int:
    m = json.load(open(manifest_path))
    backend = BACKENDS[m["backend"]](m)
    lead, tail = float(m.get("lead_pad", 0.04)), float(m.get("tail_pad", 0.14))
    retries = int(m.get("max_retries", 2)) if m["backend"] in {"chatterbox", "elevenlabs"} else 0
    results: dict = {}
    for task in m["tasks"]:
        words = max(1, len(task["text"].split()))
        best = None
        try:
            for attempt in range(retries + 1):
                audio, sr = backend.synth(task, attempt)
                audio = trim_and_pad(audio, sr, lead, tail)
                dur = len(audio) / sr
                wps = words / max(0.1, dur - lead - tail)
                sane = 1.2 <= wps <= 5.0  # outside this the model mumbled, looped or skipped text
                if best is None or sane:
                    best = (audio, sr, dur, wps, sane)
                if sane:
                    break
            audio, sr, dur, wps, sane = best  # type: ignore[misc]
            write_wav(task["out"], audio, sr)
            results[task["id"]] = {"ok": True, "duration": round(dur, 3), "sr": sr, "wps": round(wps, 2),
                                   "warn": None if sane else f"suspicious pacing {wps:.2f} words/s"}
        except Exception as e:  # one bad scene must not kill the batch
            traceback.print_exc()
            results[task["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        print(f"[tts] {task['id']}: {results[task['id']]}", flush=True)
    json.dump({"results": results}, open(out_path, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
