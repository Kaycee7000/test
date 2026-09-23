# Shorts Factory

A quality-gated, multi-niche Shorts production pipeline built to run on a rented RunPod GPU and produce
**30-50 publish-ready vertical videos per day**, delivered to Backblaze B2 grouped by niche.

```
daily trend brief (Claude live web search per niche) → trend-driven + evergreen topic backlog
→ Claude script (fact-checked, critic-scored, rewritten until it clears the bar)
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
| **Trends** | Once a day per channel, Claude runs a live web search for what the niche's audience is into right now (news, releases, anniversaries, viral stories). The brief goes into the topic prompt, and a few fresh trend-driven ideas jump the queue so they're made while timely |
| **Writing** | Claude (`claude-opus-5`, adaptive thinking, structured outputs) writes for its target platform (YouTube Shorts, TikTok or Reels rules); a critic scores hook/retention/clarity/payoff/originality, a fact-checker verifies every claim, and weak drafts are rewritten or replaced |
| **Voice** | Chatterbox (MIT, expressive, voice cloning, 23 languages) · Kokoro (Apache-2.0, fast) · ElevenLabs (API) |
| **Music** | `shorts music` generates an instrumental library per mood on the GPU with ACE-Step 1.5 (MIT; commercial use of the output allowed), or drop in licensed tracks; mix both freely |
| **Visuals** | Z-Image-Turbo by default (Apache-2.0); any diffusers model (FLUX.1-dev, Qwen-Image…). Optional Wan 2.2 image-to-video for the hook |
| **Edit** | Sub-pixel smooth camera moves, crossfades, word-by-word highlighted captions, a headline card, sidechain-ducked music, transition whooshes, loudness normalization |
| **Delivery** | Backblaze B2 (S3-compatible API, size-verified uploads) grouped `niche/language/date`, a post kit per video, a daily manifest with 7-day download links, local cleanup after upload. Direct YouTube upload remains available (`publish.mode: youtube`) |
| **Team handoff** | The YouTube team posts; the content system never touches their channels. They drop a filled manifest or a Studio export into `feedback/` and the next run learns from it: per-format and per-hook win rates drive the format mix and the writer's "what works here" examples. See [docs/TEAM_HANDOFF.md](docs/TEAM_HANDOFF.md) |
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
shorts music                                                           # one-time AI music library
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
| `shorts doctor` | Check ffmpeg/libass, fonts, GPU, TTS venv, Claude API access (free call), voices, music, storage |
| `shorts music [--moods a,b] [--count N]` | Generate AI background music until each mood folder holds N tracks |
| `shorts trends --channel ID [--refresh]` | Show today's trend brief (Claude web search; runs it if needed) |
| `shorts feedback pull` / `shorts feedback import FILE` | Import the posting team's performance CSVs |
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
- `config/channels/*.yaml`: one file per channel: niche, `niche_slug` (the storage folder), `trend_focus` (what the daily trend search looks for), language, audience, brief, pillars, banned topics, formats, art direction, music moods, narrator voice, suggested posting windows, ramp. Add a channel by adding a file.
- Env: `ANTHROPIC_API_KEY` (+ `ANTHROPIC_WORKSPACE_ID` if the key isn't workspace-scoped), `B2_KEY_ID`, `B2_APPLICATION_KEY` (required), `B2_BUCKET`, `B2_S3_ENDPOINT`, plus `SHORTS_WORKDIR`, `SHORTS_ASSETS_DIR`, `SHORTS_SECRETS_DIR`, `SHORTS_TTS_PYTHON`, `SHORTS_PUBLISH_MODE`.

## Honest expectations

- YouTube demonetizes mass-produced, templated content wherever you post from. The videos are designed around originality, accuracy, a consistent voice and AI disclosure; keep a human skimming each batch (`shorts review`).
- Claude API is the main cost, and it depends on how many drafts clear the quality bar. The first real run (Opus 5) cost $14.64 for one delivered video, most of it wasted on retries and rejected drafts that have since been cut down. Every run prints a per-step cost table; measure before scaling up. GPU time is a few dollars a day; B2 storage is small (about 35 MB per video). Details in the RunPod guide.

## Layout

```
config/            settings.yaml + channels/*.yaml
shorts_factory/    content/ (Claude, trends, topics, strategy, platforms)
                   media/ (render, captions, audio)
                   workers/ (GPU subprocesses)  publish/ (B2 storage, YouTube, scheduling)  pipeline.py  cli.py
scripts/           runpod_bootstrap.sh  prefetch_models.py  daily_run.sh  fetch_fonts.sh
assets/            fonts/  music/<mood>/  sfx/whoosh/  voices/   (see READMEs; licensed media only)
docs/              NICHE_STRATEGY  TEAM_HANDOFF  CHANNEL_PLAYBOOK  RUNPOD_GUIDE  ARCHITECTURE
tests/             unit tests + full mocked end-to-end render
```
