# Shorts Factory

A quality-gated, multi-niche Shorts production pipeline built to run on a rented RunPod GPU and produce
**30-50 publish-ready vertical videos per day**, delivered to Backblaze B2 grouped by niche.

```
topic backlog → Claude script (fact-checked, critic-scored, rewritten until it clears the bar)
→ cloned-voice narration → word-timed captions → cinematic AI images (+ optional animated hook)
→ motion, captions, ducked music, SFX, -14 LUFS → QC → optional human approval
→ B2: <niche>/<language>/<date>/<video>.mp4 + post kit (.json) + preview (.jpg) + daily _manifest.csv
```

## What each video looks like

A **35-50 second vertical (1080x1920) narrated micro-documentary**: one true, surprising story per video.

| Time | What you see and hear |
|---|---|
| 0-3 s | A bold headline card ("A VILLAGE VANISHED") over a striking image; the narrator opens with the most shocking verified detail, no intro |
| 3-40 s | 10-14 AI-generated cinematic scenes, a new one every 2.5-5 s, each with a slow camera move (push-in, pull-out, pan) and quick crossfades; big word-by-word captions with the spoken word highlighted yellow and key names/numbers in green; a new twist every 5-7 s |
| 40-50 s | The payoff, then a last line that flows back into the first so the video loops |
| Throughout | An expressive narrator (your cloned voice), mood music ducked under speech, whooshes on cuts, loudness at -14 LUFS |

Art direction per niche: dramatic oil-painting style for **history**, moody photoreal editorial noir for **money**,
photoreal space art for **space**. With `animate.enabled`, the opening scene becomes real AI video motion (Wan 2.2).
Each video ships with a post kit: title, description with hashtags, tags, a pinned-comment question, a suggested
posting time, and the AI-disclosure reminder.

## What's in the box

| | |
|---|---|
| **Niche strategy** | 4 ready-to-run channels: *Untold History* (EN flagship), *Historia Oculta* (ES), *Billion Dollar Blunders* (money stories), *Cosmic Scale* (space). Rationale in [docs/NICHE_STRATEGY.md](docs/NICHE_STRATEGY.md) |
| **Writing** | Claude (`claude-opus-5`, adaptive thinking, structured outputs) writes, a critic scores hook/retention/clarity/payoff/originality, a web-search fact-checker verifies every claim, and weak drafts are rewritten or replaced |
| **Voice** | Chatterbox (MIT, expressive, voice cloning, 23 languages) · Kokoro (Apache-2.0, fast) · ElevenLabs (API) |
| **Visuals** | Z-Image-Turbo by default (Apache-2.0); any diffusers model (FLUX.1-dev, Qwen-Image…). Optional Wan 2.2 image-to-video for the hook |
| **Edit** | Sub-pixel smooth camera moves, crossfades, word-by-word highlighted captions, a headline card, sidechain-ducked music, transition whooshes, loudness normalization |
| **Delivery** | Backblaze B2 (S3-compatible API, size-verified uploads) grouped `niche/language/date`, a post kit per video, a daily manifest with 7-day download links, local cleanup after upload. Direct YouTube upload remains available (`publish.mode: youtube`) |
| **Growth loop** | Only active with `publish.mode: youtube`: metrics sync → per-format and per-hook win rates → Thompson-sampled format mix + "what works here" examples fed to the writer. With B2 delivery, formats follow the weights in the channel YAMLs |
| **Ops** | Stage-batched GPU workers, resumable SQLite state machine, daily HTML review page, `doctor`, `--mock` mode, tests that render real MP4s |

## Quick start

On your laptop, to see it work with no GPU or API key (everything mocked, real rendering):
```bash
cd shorts-factory
pip install -e ".[dev]"            # needs ffmpeg with libass on PATH
bash scripts/fetch_fonts.sh
shorts --mock make --channel history_en --topic "The village that vanished in 1908"
pytest -q
```

On RunPod, see [docs/RUNPOD_GUIDE.md](docs/RUNPOD_GUIDE.md):
```bash
bash scripts/runpod_bootstrap.sh
python scripts/prefetch_models.py
shorts doctor
shorts make --channel history_en --topic "The Great Emu War of 1932"   # tune on single videos first
shorts run --date tomorrow                                             # a full day for every channel
```

Delivery needs a B2 bucket and an application key (`B2_KEY_ID`, `B2_APPLICATION_KEY`); setup is in the RunPod guide.
[docs/CHANNEL_PLAYBOOK.md](docs/CHANNEL_PLAYBOOK.md) covers posting the videos: cadence, AI disclosure, music
licensing, and what to watch in analytics.

## Commands

| Command | What it does |
|---|---|
| `shorts init` | Create folders and the database |
| `shorts doctor` | Check ffmpeg/libass, fonts, GPU, TTS venv, API key, voices, music, OAuth tokens |
| `shorts topics --channel ID [--count 60] [--show]` | Grow or list a channel's topic backlog |
| `shorts make --channel ID --topic "…" [--format F] [--upload]` | One video, end to end (`--upload` also delivers it to B2) |
| `shorts run [--date tomorrow] [--channels a,b] [--stages …] [--count N]` | A full day's batch, delivered to B2 |
| `shorts review --date D` | HTML review page (videos, scripts, critic scores) |
| `shorts approve [IDS…] [--date D]` / `shorts reject ID` | Editorial gate |
| `shorts auth --channel ID` | OAuth a channel (only for `publish.mode: youtube`) |
| `shorts sync` | Pull YouTube analytics (only for `publish.mode: youtube`) |
| `shorts report --channel ID` | Win rates by format and hook + current writer learnings |
| `shorts status [--date D]` | Job counts per state, recent failures |

Add `--mock` to any command to use stand-ins for every model and API.

## Configuration

- `config/settings.yaml`: models, quality thresholds, caption style, render settings, and `storage` (bucket, endpoint, key layout, cleanup).
- `config/channels/*.yaml`: one file per channel: niche, `niche_slug` (the storage folder), language, audience, brief, pillars, banned topics, formats, art direction, music moods, narrator voice, suggested posting windows, ramp. Add a channel by adding a file.
- Env: `B2_KEY_ID`, `B2_APPLICATION_KEY` (required), `B2_BUCKET`, `B2_S3_ENDPOINT`, plus `SHORTS_WORKDIR`, `SHORTS_ASSETS_DIR`, `SHORTS_SECRETS_DIR`, `SHORTS_TTS_PYTHON`, `SHORTS_PUBLISH_MODE`.

## Honest expectations

- YouTube demonetizes mass-produced, templated content wherever you post from. The videos are designed around originality, accuracy, a consistent voice and AI disclosure; keep a human skimming each batch (`shorts review`).
- Estimated running cost per 100 videos: about $30-70 of Claude API (roughly $0.30-0.70 per video on Opus 5; every run prints actual usage) plus about $4-6 of GPU time. B2 storage is small (about 35 MB per video). Details in the RunPod guide.

## Layout

```
config/            settings.yaml + channels/*.yaml
shorts_factory/    content/ (Claude, topics, strategy)  media/ (render, captions, audio)
                   workers/ (GPU subprocesses)  publish/ (B2 storage, YouTube, scheduling)  pipeline.py  cli.py
scripts/           runpod_bootstrap.sh  prefetch_models.py  daily_run.sh  fetch_fonts.sh
assets/            fonts/  music/<mood>/  sfx/whoosh/  voices/   (see READMEs; licensed media only)
docs/              NICHE_STRATEGY  CHANNEL_PLAYBOOK  RUNPOD_GUIDE  ARCHITECTURE
tests/             unit tests + full mocked end-to-end render
```
