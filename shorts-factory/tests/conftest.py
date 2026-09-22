from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A throwaway project dir: real channel configs, scratch data/assets, synthetic music + sfx."""
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "settings.yaml", tmp_path / "config" / "settings.yaml")
    shutil.copytree(ROOT / "config" / "channels", tmp_path / "config" / "channels")
    fonts = ROOT / "assets" / "fonts"
    (tmp_path / "assets" / "fonts").mkdir(parents=True)
    for f in fonts.glob("*.ttf"):
        shutil.copy(f, tmp_path / "assets" / "fonts")
    if shutil.which("ffmpeg"):
        music = tmp_path / "assets" / "music" / "dark_ambient"
        music.mkdir(parents=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=110:d=30",
                        "-ac", "2", str(music / "pad.wav")], check=True)
        sfx = tmp_path / "assets" / "sfx" / "whoosh"
        sfx.mkdir(parents=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=d=0.4:c=pink",
                        str(sfx / "w.wav")], check=True)
    return tmp_path


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
