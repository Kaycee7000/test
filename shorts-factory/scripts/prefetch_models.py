"""Download all model weights to HF_HOME (put it on the network volume) so daily runs start instantly.

usage: python scripts/prefetch_models.py [--config config/settings.yaml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from huggingface_hub import snapshot_download  # noqa: E402

from shorts_factory.config import load_settings  # noqa: E402

WHISPER = {  # faster-whisper model name -> CTranslate2 repo
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v2": "Systran/faster-whisper-large-v2",
    "medium": "Systran/faster-whisper-medium",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/settings.yaml")
    s = load_settings(ap.parse_args().config)
    repos: list[str] = []
    if s.images.backend == "diffusers":
        repos.append(s.images.model)
    if s.align.backend == "faster_whisper":
        repos.append(WHISPER.get(s.align.model, s.align.model))
    if s.tts.backend == "chatterbox":
        repos.append("ResembleAI/chatterbox")
        if s.tts.chatterbox.get("variant") == "turbo":
            repos.append("ResembleAI/chatterbox-turbo")
    if s.tts.backend == "kokoro":
        repos.append("hexgrad/Kokoro-82M")
    if s.animate.enabled:
        repos.append(s.animate.model)
    if s.research.enabled and s.research.embedder in ("auto", "local"):
        repos.append(s.research.embed_model)
    for repo in repos:
        print(f"==> {repo}", flush=True)
        try:
            snapshot_download(repo)
        except Exception as e:  # gated repo without HF_TOKEN, renamed repo, etc.
            print(f"    skipped: {e}", flush=True)


if __name__ == "__main__":
    main()
