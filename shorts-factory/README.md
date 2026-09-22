# Shorts Factory

A quality-gated, multi-channel YouTube Shorts pipeline built to run on a rented RunPod GPU and produce
**30-50 publish-ready Shorts per day** across several channels, then learn from their analytics.

```
topic backlog → Claude script (fact-checked, critic-scored, rewritten until it clears the bar)
→ cloned-voice narration → word-timed captions → cinematic AI images (+ optional animated hook)
→ motion, captions, ducked music, SFX, -14 LUFS → QC → optional human approval
→ scheduled upload with AI disclosure → analytics → better formats and hooks tomorrow
```

## What's in the box

| | |
|---|---|
| **Niche strategy** | 4 ready-to-run channels: *Untold History* (EN flagship), *Historia Oculta* (ES), *Billion Dollar Blunders* (money stories), *Cosmic Scale* (space). Rationale in [docs/NICHE_STRATEGY.md](docs/NICHE_STRATEGY.md) |
| **Writing** | Claude (`claude-opus-5`, adaptive thinking, structured outputs) writes, a critic scores hook/retention/clarity/payoff/originality, a web-search fact-checker verifies every claim, and weak drafts are rewritten or replaced |
| **Voice** | Chatterbox (MIT, expressive, voice cloning, 23 languages) · Kokoro (Apache-2.0, fast) · ElevenLabs (API) |
| **Visuals** | Z-Image-Turbo by default (Apache-2.0); any diffusers model (FLUX.1-dev, Qwen-Image…). Optional Wan 2.2 image-to-video for the hook |
| **Edit** | Sub-pixel smooth camera moves, crossfades, word-by-word highlighted captions, a headline card, sidechain-ducked music, transition whooshes, loudness normalization |
| **Publishing** | Resumable uploads scheduled into each audience's local peak windows, per-project quota tracking, `containsSyntheticMedia` disclosure, series playlists, channel-identity check |
| **Growth loop** | Metrics sync → per-format and per-hook win rates → Thompson-sampled format mix + "what works here" examples fed to the writer |
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

Before real uploads, work through [docs/CHANNEL_PLAYBOOK.md](docs/CHANNEL_PLAYBOOK.md): Brand Accounts, OAuth
("In production" or tokens expire weekly), the **API audit** (unaudited projects can only upload private videos),
licensed music, and the posting ramp.

## Commands

| Command | What it does |
|---|---|
| `shorts init` | Create folders and the database |
| `shorts doctor` | Check ffmpeg/libass, fonts, GPU, TTS venv, API key, voices, music, OAuth tokens |
| `shorts topics --channel ID [--count 60] [--show]` | Grow or list a channel's topic backlog |
| `shorts make --channel ID --topic "…" [--format F] [--upload]` | One video, end to end |
| `shorts run [--date tomorrow] [--channels a,b] [--stages …] [--count N]` | A full day's batch |
| `shorts review --date D` | HTML review page (videos, scripts, critic scores) |
| `shorts approve [IDS…] [--date D]` / `shorts reject ID` | Editorial gate |
| `shorts auth --channel ID` | OAuth a channel (on a machine with a browser) |
| `shorts sync` | Pull analytics for uploaded videos |
| `shorts report --channel ID` | Win rates by format and hook + current writer learnings |
| `shorts status [--date D]` | Job counts per state, recent failures |

Add `--mock` to any command to use stand-ins for every model and API.

## Configuration

- `config/settings.yaml`: models, quality thresholds, caption style, render and publish settings.
- `config/channels/*.yaml`: one file per channel: niche, audience, brief, pillars, banned topics, formats, art direction, music moods, narrator voice, posting windows, ramp, YouTube ids. Add a channel by adding a file.
- Env overrides: `SHORTS_WORKDIR`, `SHORTS_ASSETS_DIR`, `SHORTS_SECRETS_DIR`, `SHORTS_TTS_PYTHON`, `SHORTS_PUBLISH_MODE`.

## Honest expectations

- Shorts ad revenue is small per view. Shorts build audiences fast; long-form compilations, sponsors and affiliates are where channels make money. The playbook covers the path.
- YouTube demonetizes mass-produced, templated content. This system is designed around originality, accuracy, a consistent voice, AI disclosure and human review. Keep the review habit even when fully automated.
- Estimated running cost at ~42 videos/day: about $60-80/mo of GPU and storage, plus the Claude API (roughly $0.30-0.70 per video on Opus 5; every run prints actual usage). Details in the RunPod guide.

## Layout

```
config/            settings.yaml + channels/*.yaml
shorts_factory/    content/ (Claude, topics, strategy)  media/ (render, captions, audio)
                   workers/ (GPU subprocesses)  publish/ (YouTube, scheduling)  pipeline.py  cli.py
scripts/           runpod_bootstrap.sh  prefetch_models.py  daily_run.sh  fetch_fonts.sh
assets/            fonts/  music/<mood>/  sfx/whoosh/  voices/   (see READMEs; licensed media only)
docs/              NICHE_STRATEGY  CHANNEL_PLAYBOOK  RUNPOD_GUIDE  ARCHITECTURE
tests/             unit tests + full mocked end-to-end render
```
