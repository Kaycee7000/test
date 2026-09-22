"""AI background-music library: tops up assets/music/<mood>/ with instrumental tracks generated on the GPU.

Runs once per library (`shorts music`), not per video. The renderer keeps picking tracks from the mood
folders exactly as it does for licensed music, so generated and licensed tracks can sit side by side.
"""
from __future__ import annotations

import csv
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from ..config import ChannelCfg, Settings
from .music import _files
from .workers import run_worker

log = logging.getLogger(__name__)

# Written for music that sits UNDER narration: steady energy, no vocals, nothing that fights the voice.
MOOD_PROMPTS: dict[str, tuple[str, int]] = {
    "dark_ambient": ("dark cinematic ambient underscore, low drones, sparse felt piano, distant percussion, "
                     "brooding, slow build", 70),
    "mystery": ("mysterious documentary underscore, pizzicato strings, ticking percussion, eerie celesta, "
                "curious and suspenseful", 90),
    "epic": ("epic orchestral underscore, driving taiko drums, low brass, string ostinato, heroic, "
             "building intensity", 110),
    "tension": ("tense thriller underscore, pulsing synth bass, ticking hi-hats, rising strings, suspense", 100),
    "corporate_dark": ("dark modern corporate underscore, minimal electronic beat, deep sub bass, muted plucks, "
                       "sleek and serious", 95),
    "cinematic": ("cinematic emotional underscore, warm strings, felt piano, gentle percussion, "
                  "inspiring build", 85),
    "space_ambient": ("cosmic ambient soundscape, evolving synth pads, shimmering textures, slow and vast, "
                      "no drums", 60),
    "wonder": ("awe-inspiring cinematic underscore, soaring strings, glockenspiel sparkle, uplifting and curious", 95),
}
SUFFIX = "instrumental, no vocals, background score for spoken narration"
# Rotated across a mood's tracks so twenty tracks of one mood don't all sound alike.
VARIATIONS = ["cello lead", "analog synth textures", "solo piano motif", "string ostinato", "plucked harp",
              "muted electric guitar", "hand percussion", "warm brass swells"]
LOG_NAME = "ai_tracks.csv"  # licence record: file, model, caption, seed


def acestep_python(s: Settings) -> str:
    return s.music.python or str(s.path(s.music.acestep_dir) / ".venv" / "bin" / "python")


def library_moods(channels: list[ChannelCfg]) -> list[str]:
    """Every mood the channels use, in first-seen order."""
    return list(dict.fromkeys(m for ch in channels for m in ch.music_moods))


def mood_prompt(s: Settings, mood: str, i: int) -> tuple[str, int | None]:
    """Caption + BPM for the i-th track of a mood. `music.prompts` in settings.yaml overrides the table."""
    if mood in s.music.prompts:
        base, bpm = s.music.prompts[mood], None
    elif mood in MOOD_PROMPTS:
        base, bpm = MOOD_PROMPTS[mood]
    else:
        base, bpm = f"{mood.replace('_', ' ')} documentary underscore", None
    rng = random.Random(f"{mood}:{i}")
    if bpm:
        bpm += rng.randint(-8, 8)
    return f"{base}, {VARIATIONS[i % len(VARIATIONS)]}, {SUFFIX}", bpm


def build_library(s: Settings, moods: list[str], per_mood: int) -> tuple[int, list[str]]:
    """Generate tracks until each mood folder holds `per_mood` audio files. Returns (made, errors)."""
    m = s.music
    ext = ".wav" if m.backend == "mock" else ".flac"
    root = s.assets / "music"
    tasks = []
    for mood in moods:
        have = len(_files(root / mood))
        (root / mood).mkdir(parents=True, exist_ok=True)
        for i in range(have, per_mood):
            seed = random.randrange(2 ** 31)
            caption, bpm = mood_prompt(s, mood, i)
            tasks.append({"id": f"{mood}:{i}", "mood": mood, "caption": caption, "bpm": bpm, "seed": seed,
                          "seconds": m.seconds, "out": str(root / mood / f"ai-{mood}-{seed}{ext}")})
    if not tasks:
        return 0, []
    params = {"acestep_dir": str(s.path(m.acestep_dir)), "dit_model": m.dit_model, "lm_model": m.lm_model,
              "lm_backend": m.lm_backend, "inference_steps": m.inference_steps}
    results = run_worker("music", {"backend": m.backend, "device": m.device, "params": params, "tasks": tasks},
                         s.work / "manifests", python=acestep_python(s) if m.backend == "acestep" else None,
                         require_python=m.backend == "acestep")
    made = [t for t in tasks if results.get(t["id"], {}).get("ok") and Path(t["out"]).exists()]
    errors = [f"{t['id']}: {results.get(t['id'], {}).get('error', 'no output')}" for t in tasks if t not in made]
    if made:
        logf = root / LOG_NAME
        new = not logf.exists()
        with logf.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["file", "mood", "model", "caption", "bpm", "seed", "seconds", "created_utc"])
            model = m.dit_model if m.backend == "acestep" else "mock"
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for t in made:
                w.writerow([Path(t["out"]).relative_to(root), t["mood"], model, t["caption"], t["bpm"] or "",
                            t["seed"], t["seconds"], now])
    return len(made), errors
