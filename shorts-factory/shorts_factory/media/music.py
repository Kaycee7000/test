"""Music and SFX selection from the local, licence-safe asset library."""
from __future__ import annotations

import random
from pathlib import Path

from .audio import duration

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}


def _files(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*") if p.suffix.lower() in AUDIO_EXT) if d.exists() else []


def pick_music(assets: Path, mood: str, fallback_moods: list[str], seed: str, video_len: float) -> tuple[Path, float] | None:
    """Returns (track, start offset). Offsets vary per video so the same track never sounds identical."""
    rng = random.Random(seed)
    root = assets / "music"
    for m in [mood, *fallback_moods]:
        tracks = _files(root / m)
        if tracks:
            break
    else:
        tracks = _files(root)
    if not tracks:
        return None
    track = rng.choice(tracks)
    try:
        slack = duration(track) - video_len - 2.0
    except Exception:
        slack = 0.0
    return track, (rng.uniform(0, slack) if slack > 0 else 0.0)


def pick_sfx(assets: Path, kind: str, seed: str) -> Path | None:
    files = _files(assets / "sfx" / kind)
    return random.Random(seed).choice(files) if files else None
