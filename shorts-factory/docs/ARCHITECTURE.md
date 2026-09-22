# Architecture

```mermaid
flowchart LR
    subgraph research[Research · public data only]
      S1[Scout: YouTube public search,<br/>Wikipedia most-read, on-this-day, RSS] --> KB[(Knowledge base<br/>FTS5 + embeddings)]
    end
    subgraph plan[Plan]
      A[Ramp target per channel] --> B[Thompson-sampled formats]
      B --> C[Topic backlog<br/>demand signals + semantic dedup]
      C --> D[Jobs + suggested post slots]
    end
    KB --> C
    D --> DS[Dossier per video<br/>Wikipedia + Claude web search] --> KB
    subgraph script[Script · Claude API, parallel]
      E[Writer] --> F[Lint + web fact-check]
      F --> G[Critic scores]
      G -- below bar --> E
    end
    subgraph gpu[GPU workers · one model load per batch]
      H[Voice<br/>Chatterbox / Kokoro] --> I[Word timings<br/>faster-whisper]
      I --> J[Images<br/>Z-Image / FLUX / Qwen]
      J --> K[Hook animation<br/>Wan 2.2, optional]
    end
    subgraph cpu[Render · CPU pool]
      L[Camera motion + crossfades] --> M[ASS captions] --> N[Ducked music, SFX,<br/>-14 LUFS]
    end
    DS --> E
    KB -- related notes + past videos --> E
    G -- pass --> H
    G -- approved script --> KB
    K --> L
    N --> O[QC] --> P{Approval gate}
    P --> Q[B2 storage<br/>niche/language/date<br/>+ post kit + manifest]
    Q --> T[YouTube team posts]
    T -- feedback/ CSVs --> R[Metrics] --> B
    P -. publish.mode: youtube .-> Y[Direct YouTube upload, optional]
```

## Principles

1. **Stage-batched, not video-by-video.** Each GPU model loads once per day's batch instead of 50 times. Stages run in order; within a stage, work runs in bulk.
2. **GPU stages are subprocess workers** (`shorts_factory/workers/*_worker.py`) that take a JSON manifest. VRAM is fully released between stages, a model crash can't kill the orchestrator, and a stage can use a different venv. Chatterbox pins `torch==2.6`/`diffusers==0.29`, so it gets its own; the TTS worker imports only the stdlib and numpy for that reason.
3. **Idempotent state machine.** SQLite (`data/shorts.db`) holds each job's state: `planned → researched → scripted → voiced → aligned → imaged → animated → rendered → ready → uploaded` (or `rejected` / `failed`). A stage only picks up jobs in its input state; per-scene outputs that already exist are skipped. Re-running any command resumes.
4. **Quality gates at every step.** Script: lint, fact-check, critic thresholds, up to N rewrites, reject and replace. Voice: pacing sanity check with re-synthesis. Duration: auto tempo-fit or reject. Render: QC on resolution, duration, audio level. Publish: optional human approval.
5. **Deterministic seeds** (`crc32` of job id + scene) so a re-render reproduces the same images and music choice.
6. **Everything mockable.** `--mock` swaps every model and API for local stand-ins while the real workers, renderer, ffmpeg mix and scheduler run. The test suite renders real MP4s this way.

## Code map

| Path | Role |
|---|---|
| `shorts_factory/config.py` | Typed settings and channel configs (pydantic) + env overrides |
| `shorts_factory/db.py` | SQLite schema, job states, metrics |
| `shorts_factory/pipeline.py` | Orchestrator: every stage + `render_job` for the process pool |
| `research/kb.py` | Knowledge base: SQLite + FTS5 + embeddings, hybrid retrieval, retention, backups |
| `research/embed.py` | Embedding backends (bge-m3 via sentence-transformers; hash for tests) |
| `research/collectors.py` | Public-data collectors: YouTube (API key), Wikipedia, RSS |
| `research/scout.py` | Daily scouting per channel and the trend brief for the strategist |
| `research/dossier.py` | Per-video research dossier and the writer's retrieval context |
| `content/platforms.py` | What each destination platform rewards (YouTube Shorts, TikTok, Reels) |
| `publish/feedback.py` | Imports the posting team's CSVs (filled manifest or Studio export) into metrics |
| `content/llm.py` | Claude client (structured outputs, adaptive thinking, refusal fallback, web search) + mock |
| `content/prompts.py` | Writer / critic / fact-check / topic prompts (stable system prompts per channel) |
| `content/writer.py` | Draft → lint → fact-check → critique → rewrite loop |
| `content/topics.py` | Backlog generation and duplicate detection |
| `content/strategy.py` | Ramp, win/loss outcomes, Thompson sampling, learnings for prompts |
| `media/render.py` | OpenCV frame compositor piped into ffmpeg; audio graph; encoder choice |
| `media/captions.py` | Word-by-word ASS captions + headline card |
| `media/wordmap.py` | Aligns ASR word times onto the exact script words |
| `media/workers.py` | Launches GPU workers with manifests |
| `workers/*.py` | TTS, alignment, image and animation workers |
| `publish/schedule.py` | Publish slots inside local peak windows |
| `publish/storage.py` | B2 (S3-compatible) and local delivery, post kits, daily manifests, local cleanup |
| `publish/youtube.py` | Optional: OAuth, resumable scheduled uploads, playlists, metrics, dry-run publisher |
| `review.py` | Daily HTML review page |

## Job folder layout

```
data/jobs/2026-09-23/history_en/20260923-history_en-1a2b3c/
  research.md            the sourced research dossier
  script.json            final approved script (or script.rejected.json with critic notes)
  voice/scene_XX.wav     per-scene narration (trimmed, padded, tempo-fitted)
  voice.wav              full narration
  words/ words.json      word timings (script spelling, global times, emphasis flags)
  images/scene_XX.png    generated stills
  clips/scene_XX.mp4     animated hook scene(s), if enabled
  captions.ass           burned-in captions
  final.mp4              1080x1920 H.264 High, 30 fps, AAC 48 kHz, -14 LUFS
  preview.jpg            QC frame
```
After a verified B2 upload, `storage.cleanup_local: media` deletes the media files above and keeps the small
text files (script, words, captions). In the bucket:

```
history/en/2026-09-23/<job id>_<title-slug>.mp4    the video
history/en/2026-09-23/<job id>_<title-slug>.json   post kit: title, description + hashtags, tags,
                                                   pinned-comment question, suggested time, scores
history/en/2026-09-23/<job id>_<title-slug>.jpg    preview frame
history/en/2026-09-23/_manifest.csv / .json        the day's videos for this niche + language, in posting order
```

## Extending

- **New channel:** copy a YAML in `config/channels/`, change niche, brief, formats, visual style, voice and schedule. No code.
- **New image/video model:** anything `DiffusionPipeline.from_pretrained` can load works via `images.model`, `steps`, `guidance_scale` and `extra`.
- **New TTS:** add a `Backend` subclass in `workers/tts_worker.py`.
- **Long-form compilations** (next milestone): select top Shorts by `shorts report`, concatenate with chapter cards, upload as 16:9 or 9:16 long-form.
