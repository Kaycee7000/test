"""Typed configuration: global settings (settings.yaml) + one YAML per channel."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class LLMCfg(BaseModel):
    provider: Literal["anthropic", "mock"] = "anthropic"
    model: str = "claude-opus-5"
    effort: Effort = "high"
    critic_effort: Effort = "high"
    fallbacks: bool = True  # server-side refusal fallback (routes declined requests to another model)
    concurrency: int = 6
    max_rewrites: int = 2
    # A script must clear every threshold to ship; otherwise it is rewritten, then rejected.
    min_hook_score: int = 8
    min_overall_score: float = 7.5
    web_search_max_uses: int = 5


class TTSCfg(BaseModel):
    backend: Literal["chatterbox", "kokoro", "elevenlabs", "mock"] = "chatterbox"
    python: str | None = None  # interpreter for the worker; chatterbox needs its own venv
    device: str = "cuda"
    speed: float = 1.0  # post-TTS tempo change (ffmpeg atempo); kokoro also has a native speed
    lead_pad: float = 0.04  # silence kept before each scene's speech after trimming
    tail_pad: float = 0.14  # silence kept after each scene's speech (breath between scenes)
    max_retries: int = 2  # re-synthesize scenes whose pacing looks broken (hallucinated audio)
    chatterbox: dict[str, Any] = Field(default_factory=lambda: {
        "variant": "standard", "exaggeration": 0.55, "cfg_weight": 0.45, "temperature": 0.8,
    })
    kokoro: dict[str, Any] = Field(default_factory=lambda: {"speed": 1.08})
    elevenlabs: dict[str, Any] = Field(default_factory=lambda: {
        "model_id": "eleven_multilingual_v2", "stability": 0.45, "similarity_boost": 0.8, "style": 0.25,
    })


class AlignCfg(BaseModel):
    backend: Literal["faster_whisper", "mock"] = "faster_whisper"
    python: str | None = None
    model: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"


class ImageCfg(BaseModel):
    backend: Literal["diffusers", "mock"] = "diffusers"
    python: str | None = None
    model: str = "Tongyi-MAI/Z-Image-Turbo"
    width: int = 1088
    height: int = 1920
    steps: int = 9
    guidance_scale: float = 0.0
    negative_prompt: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)  # model-specific call kwargs, e.g. true_cfg_scale
    cpu_offload: bool = False
    device: str = "cuda"


class AnimateCfg(BaseModel):
    """Optional image-to-video for the hook scene(s). Heavy: size the GPU before enabling."""
    enabled: bool = False
    python: str | None = None
    scenes: Literal["hook", "all"] = "hook"
    hook_scenes: int = 1
    model: str = "Wan-AI/Wan2.2-I2V-A14B-Diffusers"
    num_frames: int = 81
    fps: int = 16
    steps: int = 40
    guidance_scale: float = 3.5
    max_area: int = 480 * 832
    cpu_offload: bool = True
    device: str = "cuda"


class CaptionCfg(BaseModel):
    font: str = "Anton"
    size: int = 124
    uppercase: bool = True
    max_words: int = 3
    max_chars: int = 18
    primary: str = "#FFFFFF"
    highlight: str = "#FFE14D"
    emphasis: str = "#3DFF7A"
    outline: int = 8
    shadow: int = 2
    center_y: int = 1150  # keeps captions clear of the Shorts UI (bottom ~20%, right rail)
    headline: bool = True
    headline_seconds: float = 2.8
    headline_size: int = 92
    headline_y: int = 360


class RenderCfg(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    transition: float = 0.12  # crossfade seconds at scene cuts (0 = hard cut)
    zoom_strength: float = 0.12
    pan_strength: float = 0.22
    encoder: Literal["auto", "libx264", "h264_nvenc"] = "auto"
    crf: int = 18
    preset: str = "medium"
    workers: int = 2
    tail: float = 0.35
    min_duration: float = 15.0
    max_duration: float = 59.0
    music_db: float = -19.0
    sfx_db: float = -9.0
    loudness_lufs: float = -14.0
    transition_sfx: bool = True


class PublishCfg(BaseModel):
    mode: Literal["youtube", "dry_run"] = "dry_run"
    secrets_dir: str = "secrets"
    client_secrets: str = "client_secret.json"
    max_uploads_per_project_per_day: int = 100
    lead_minutes: int = 45  # never schedule a publish slot sooner than this from now
    notify_subscribers: bool = False
    require_approval: bool = False  # human editorial gate: only `shorts approve`d videos upload


class Settings(BaseModel):
    workdir: str = "data"
    assets_dir: str = "assets"
    channels_dir: str = "config/channels"
    llm: LLMCfg = LLMCfg()
    tts: TTSCfg = TTSCfg()
    align: AlignCfg = AlignCfg()
    images: ImageCfg = ImageCfg()
    animate: AnimateCfg = AnimateCfg()
    captions: CaptionCfg = CaptionCfg()
    render: RenderCfg = RenderCfg()
    publish: PublishCfg = PublishCfg()

    # Resolved at load time so relative paths work from any cwd.
    root: str = "."

    def path(self, p: str | os.PathLike) -> Path:
        q = Path(p)
        return q if q.is_absolute() else Path(self.root) / q

    @property
    def work(self) -> Path:
        return self.path(self.workdir)

    @property
    def assets(self) -> Path:
        return self.path(self.assets_dir)

    def mock(self) -> "Settings":
        """Every external/GPU dependency swapped for a deterministic local stand-in."""
        s = self.model_copy(deep=True)
        s.llm.provider = "mock"
        s.tts.backend = "mock"
        s.align.backend = "mock"
        s.images.backend = "mock"
        s.animate.enabled = False
        s.publish.mode = "dry_run"
        return s


# ---------------------------------------------------------------- channels


class FormatCfg(BaseModel):
    id: str
    name: str
    description: str
    structure: str
    weight: float = 1.0
    playlist_id: str | None = None


class RampStep(BaseModel):
    days: int | None = None  # None = open-ended final step
    per_day: int


class ScheduleCfg(BaseModel):
    timezone: str = "America/New_York"
    windows: list[str] = Field(default_factory=lambda: ["07:00-09:30", "11:30-14:00", "16:30-22:30"])
    min_gap_minutes: int = 45
    launch_date: date | None = None
    ramp: list[RampStep] = Field(default_factory=lambda: [RampStep(per_day=3)])

    @field_validator("windows")
    @classmethod
    def _check_windows(cls, v: list[str]) -> list[str]:
        for w in v:
            a, b = w.split("-")
            if _minutes(a) >= _minutes(b):
                raise ValueError(f"window {w!r} must be HH:MM-HH:MM with start < end (same day)")
        return v


def _minutes(hhmm: str) -> int:
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


class VoiceCfg(BaseModel):
    chatterbox_ref: str | None = None  # 10-20s clean reference clip = the channel's signature voice
    kokoro_voice: str = "am_michael"
    elevenlabs_voice_id: str | None = None
    exaggeration: float | None = None


class YouTubeCfg(BaseModel):
    channel_id: str | None = None  # expected channel; uploads abort if the token belongs to another
    token_file: str | None = None  # defaults to <secrets_dir>/<channel id>.token.json
    project: str = "default"  # Google Cloud project the token was minted with (quota bucket)
    category_id: str = "27"  # 27 Education, 24 Entertainment, 28 Science & Tech
    contains_synthetic_media: bool = True
    made_for_kids: bool = False


class ChannelCfg(BaseModel):
    id: str
    name: str
    enabled: bool = True
    language: str = "en"
    niche: str
    audience: str
    brief: str
    pillars: list[str]
    banned: list[str] = Field(default_factory=list)
    formats: list[FormatCfg]
    visual_style: str
    negative_prompt: str | None = None
    music_moods: list[str] = Field(default_factory=lambda: ["cinematic"])
    target_seconds: int = 45
    words_per_second: float = 2.6
    fact_check: bool = True
    voice: VoiceCfg = VoiceCfg()
    schedule: ScheduleCfg = ScheduleCfg()
    youtube: YouTubeCfg = YouTubeCfg()

    def format(self, fid: str) -> FormatCfg:
        for f in self.formats:
            if f.id == fid:
                return f
        return self.formats[0]

    @property
    def target_words(self) -> tuple[int, int]:
        mid = self.target_seconds * self.words_per_second
        return int(mid * 0.88), int(mid * 1.1)


# ---------------------------------------------------------------- loading


def load_settings(path: str | os.PathLike = "config/settings.yaml") -> Settings:
    p = Path(path).resolve()
    data: dict[str, Any] = {}
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
    # settings live in <root>/config/settings.yaml
    root = p.parent.parent if p.parent.name == "config" else p.parent
    data.setdefault("root", str(root))
    s = Settings.model_validate(data)
    # Environment overrides let one config serve the pod, a Docker image and a laptop.
    env = os.environ
    if env.get("SHORTS_WORKDIR"):
        s.workdir = env["SHORTS_WORKDIR"]
    if env.get("SHORTS_ASSETS_DIR"):
        s.assets_dir = env["SHORTS_ASSETS_DIR"]
    if env.get("SHORTS_SECRETS_DIR"):
        s.publish.secrets_dir = env["SHORTS_SECRETS_DIR"]
    if env.get("SHORTS_TTS_PYTHON"):
        s.tts.python = env["SHORTS_TTS_PYTHON"]
    if env.get("SHORTS_PUBLISH_MODE") in ("youtube", "dry_run"):
        s.publish.mode = env["SHORTS_PUBLISH_MODE"]  # type: ignore[assignment]
    return s


def load_channels(settings: Settings, only: list[str] | None = None) -> list[ChannelCfg]:
    out = []
    for f in sorted(settings.path(settings.channels_dir).glob("*.yaml")):
        ch = ChannelCfg.model_validate(yaml.safe_load(f.read_text()))
        if only and ch.id not in only:
            continue
        if ch.enabled or only:
            out.append(ch)
    return out
