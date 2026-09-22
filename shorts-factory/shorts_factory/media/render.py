"""Final assembly: sub-pixel smooth camera motion over stills/clips, crossfades, burned-in captions,
ducked music, transition SFX, loudness normalization. Frames are generated with OpenCV and piped
straight into ffmpeg, so no intermediate video files are written."""
from __future__ import annotations

import logging
import math
import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from ..config import RenderCfg
from .audio import ffmpeg_bin

log = logging.getLogger(__name__)


@dataclass
class Scene:
    media: str
    kind: str  # image | video
    motion: str
    start: float
    end: float


def ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 0.5 - 0.5 * math.cos(math.pi * p)


def cover_resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = img.shape[:2]
    scale = max(w / iw, h / ih)
    nw, nh = max(w, round(iw * scale)), max(h, round(ih * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4
    r = cv2.resize(img, (nw, nh), interpolation=interp)
    x, y = (nw - w) // 2, (nh - h) // 2
    return r[y:y + h, x:x + w]


class Source:
    """One scene's pixels plus its camera move. frame(p) returns a W x H BGR frame at progress p in [0,1]."""

    def __init__(self, scene: Scene, W: int, H: int, zoom_strength: float, pan_strength: float):
        self.W, self.H, self.motion = W, H, scene.motion
        self.frames: list[np.ndarray] | None = None
        if scene.kind == "video":
            cap = cv2.VideoCapture(scene.media)
            self.clip_fps = cap.get(cv2.CAP_PROP_FPS) or 16.0
            frames = []
            while True:
                ok, f = cap.read()
                if not ok:
                    break
                frames.append(cover_resize(f, W, H))
            cap.release()
            if not frames:
                raise RuntimeError(f"could not decode {scene.media}")
            self.frames = frames
            self.scene_dur = max(0.1, scene.end - scene.start)
            return
        img = cv2.imread(scene.media, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"could not read {scene.media}")
        strength = pan_strength if scene.motion.startswith("pan") else zoom_strength
        self.zmax = 1.0 + strength
        self.SW, self.SH = round(W * self.zmax), round(H * self.zmax)
        self.src = cover_resize(img, self.SW, self.SH)

    def frame(self, p: float) -> np.ndarray:
        if self.frames is not None:
            return self._video_frame(p)
        e = ease(p)
        m = self.motion
        if m == "zoom_out":
            z = self.zmax - (self.zmax - 1.0) * e
        elif m.startswith("pan"):
            z = self.zmax
        else:  # zoom_in (default)
            z = 1.0 + (self.zmax - 1.0) * e
        ww, wh = self.SW / z, self.SH / z  # visible window in source pixels
        cx, cy = self.SW / 2, self.SH / 2
        sx, sy = (self.SW - ww) / 2 * 0.9, (self.SH - wh) / 2 * 0.9  # slack for panning
        if m == "pan_left":
            cx += sx * (1 - 2 * e)
        elif m == "pan_right":
            cx -= sx * (1 - 2 * e)
        elif m == "pan_up":
            cy += sy * (1 - 2 * e)
        elif m == "pan_down":
            cy -= sy * (1 - 2 * e)
        a, d = ww / self.W, wh / self.H
        M = np.array([[a, 0, cx - ww / 2], [0, d, cy - wh / 2]], dtype=np.float64)
        return cv2.warpAffine(self.src, M, (self.W, self.H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REFLECT)

    def _video_frame(self, p: float) -> np.ndarray:
        frames = self.frames
        assert frames is not None
        n = len(frames)
        clip_dur = n / self.clip_fps
        speed = min(1.0, max(0.6, clip_dur / self.scene_dur))  # slow short clips a little, never below 0.6x
        pos = max(0.0, p) * self.scene_dur * speed * self.clip_fps
        if n > 1:  # ping-pong beyond the end instead of freezing
            period = 2 * (n - 1)
            pos = pos % period
            if pos > n - 1:
                pos = period - pos
        i0 = int(math.floor(pos))
        i1 = min(n - 1, i0 + 1)
        frac = pos - i0
        if frac < 1e-3 or i0 == i1:
            return frames[min(i0, n - 1)]
        return cv2.addWeighted(frames[i0], 1.0 - frac, frames[i1], frac, 0.0)


@lru_cache(maxsize=None)
def pick_encoder(pref: str) -> str:
    if pref != "auto":
        return pref
    try:
        r = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0:
            return "h264_nvenc"
    except (subprocess.SubprocessError, OSError):
        pass
    return "libx264"


def video_args(cfg: RenderCfg) -> list[str]:
    enc = pick_encoder(cfg.encoder)
    gop = ["-g", str(max(1, cfg.fps // 2)), "-bf", "2"]  # YouTube: closed GOP of half the frame rate
    if enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", str(cfg.crf + 1),
                "-b:v", "0", "-maxrate", "24M", "-bufsize", "48M", "-profile:v", "high", "-pix_fmt", "yuv420p", *gop]
    return ["-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-profile:v", "high",
            "-pix_fmt", "yuv420p", *gop]


def audio_graph(total: float, has_music: bool, sfx_times: list[float], cfg: RenderCfg) -> str:
    """Voice (input 1) + ducked music (input 2) + transition SFX (next input) -> loudness-normalized [a].

    Every branch is padded to full length so amix never renormalizes mid-video; compatible with ffmpeg 4.4+.
    """
    D = f"{total:.3f}"
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    parts = [f"[1:a]{fmt},apad=whole_dur={D}[vpad]"]
    mix = []
    idx = 2
    if has_music:
        parts.append("[vpad]asplit=2[voice][vsc]")
        fade_out = max(0.0, total - 1.2)
        parts.append(
            f"[{idx}:a]{fmt},volume={cfg.music_db}dB,afade=t=in:st=0:d=0.6,"
            f"afade=t=out:st={fade_out:.3f}:d=1.2,apad=whole_dur={D}[mraw]"
        )
        parts.append("[mraw][vsc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[music]")
        mix += ["[voice]", "[music]"]
        idx += 1
    else:
        mix.append("[vpad]")
    if sfx_times:
        n = len(sfx_times)
        labels = "".join(f"[s{k}]" for k in range(n))
        split = f",asplit={n}" if n > 1 else ""
        parts.append(f"[{idx}:a]{fmt},volume={cfg.sfx_db}dB{split}{labels}")
        for k, t in enumerate(sfx_times):
            ms = max(0, int(t * 1000))
            parts.append(f"[s{k}]adelay=delays={ms}:all=1,apad=whole_dur={D}[d{k}]")
            mix.append(f"[d{k}]")
    if len(mix) > 1:
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:dropout_transition=0,volume={len(mix)}[mix]")
    else:
        parts.append(f"{mix[0]}anull[mix]")
    parts.append(f"[mix]loudnorm=I={cfg.loudness_lufs}:TP=-1.5:LRA=11,aresample=48000,atrim=end={D}[a]")
    return ";".join(parts)


def render(scenes: list[Scene], voice_wav: Path, ass_file: Path, out: Path, cfg: RenderCfg,
           fonts_dir: Path, total: float, music: tuple[Path, float] | None = None,
           sfx: Path | None = None) -> None:
    W, H, fps = cfg.width, cfg.height, cfg.fps
    workdir = out.parent
    sources = [Source(s, W, H, cfg.zoom_strength, cfg.pan_strength) for s in scenes]
    td = max(0.0, cfg.transition)
    boundaries = [s.start for s in scenes[1:]]
    sfx_times = [max(0.0, b - 0.18) for b in boundaries] if (sfx and cfg.transition_sfx) else []

    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-i", str(voice_wav.resolve())]
    if music:
        cmd += ["-stream_loop", "-1", "-ss", f"{music[1]:.2f}", "-t", f"{total + 1:.2f}", "-i", str(music[0].resolve())]
    if sfx_times:
        cmd += ["-i", str(sfx.resolve())]  # type: ignore[union-attr]
    fonts_rel = os.path.relpath(fonts_dir.resolve(), workdir.resolve())
    vgraph = f"[0:v]subtitles=filename={ass_file.name}:fontsdir={fonts_rel}[v]"
    cmd += ["-filter_complex", vgraph + ";" + audio_graph(total, bool(music), sfx_times, cfg),
            "-map", "[v]", "-map", "[a]", *video_args(cfg), "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{total:.3f}", "-movflags", "+faststart", out.name]

    log_path = workdir / "ffmpeg.log"
    n_frames = int(round(total * fps))
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=logf, stderr=logf, cwd=workdir)
        assert proc.stdin is not None
        try:
            si = 0
            for f in range(n_frames):
                t = f / fps
                while si + 1 < len(scenes) and t >= scenes[si + 1].start:
                    si += 1
                frame = _scene_frame(sources[si], scenes[si], t, td)
                if si + 1 < len(scenes) and td > 0 and t > scenes[si + 1].start - td / 2:
                    nb = scenes[si + 1].start
                    alpha = (t - (nb - td / 2)) / td
                    frame = cv2.addWeighted(frame, 1 - alpha, _scene_frame(sources[si + 1], scenes[si + 1], t, td), alpha, 0)
                elif si > 0 and td > 0 and t < scenes[si].start + td / 2:
                    b = scenes[si].start
                    alpha = (t - (b - td / 2)) / td
                    frame = cv2.addWeighted(_scene_frame(sources[si - 1], scenes[si - 1], t, td), 1 - alpha, frame, alpha, 0)
                proc.stdin.write(frame.tobytes())
            proc.stdin.close()
        except BrokenPipeError:
            pass
        rc = proc.wait()
    if rc != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg failed ({rc}); see {log_path}: {log_path.read_text(errors='ignore')[-1500:]}")


def _scene_frame(src: Source, scene: Scene, t: float, td: float) -> np.ndarray:
    span = (scene.end - scene.start) + td
    p = (t - (scene.start - td / 2)) / span if span > 0 else 0.0
    return src.frame(p)


def extract_preview(video: Path, out: Path, at: float = 1.0) -> None:
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "3", str(out)], check=True)
