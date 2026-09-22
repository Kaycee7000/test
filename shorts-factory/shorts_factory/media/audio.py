"""Small audio/ffmpeg helpers shared by the pipeline stages."""
from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH (apt-get install -y ffmpeg)")
    return exe


def ffprobe_bin() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe not found on PATH (apt-get install -y ffmpeg)")
    return exe


def probe(path: str | Path) -> dict:
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def duration(path: str | Path) -> float:
    return float(probe(path)["format"]["duration"])


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path}: expected 16-bit PCM")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def write_wav(path: str | Path, audio: np.ndarray, sr: int) -> None:
    a = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((a * 32767.0).astype("<i2").tobytes())


def change_tempo(src: Path, dst: Path, speed: float) -> None:
    """Pitch-preserving speed change (atempo accepts 0.5-2.0 per instance)."""
    subprocess.run(
        [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(src), "-filter:a", f"atempo={speed:.4f}",
         "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )


def concat_wavs(paths: list[Path], dst: Path) -> list[tuple[float, float]]:
    """Concatenate mono wavs; returns (start, end) of each piece in the result."""
    pieces, spans, t, sr0 = [], [], 0.0, None
    for p in paths:
        a, sr = read_wav(p)
        if sr0 is None:
            sr0 = sr
        elif sr != sr0:
            raise ValueError(f"sample-rate mismatch in {p}")
        pieces.append(a)
        d = len(a) / sr
        spans.append((t, t + d))
        t += d
    write_wav(dst, np.concatenate(pieces) if pieces else np.zeros(1, np.float32), sr0 or 24000)
    return spans


def mean_volume_db(path: str | Path) -> float:
    r = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    for line in r.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    return -99.0
