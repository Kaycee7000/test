# Shorts Factory: working notes for Claude

The project lives in `shorts-factory/` (read its README and `docs/` for the design). Work on branch
`claude/wizardly-einstein-oaemuk`. Never commit or print secrets: `/workspace/secrets.env`, `secrets/`, `.env`.

## The GPU pod (RunPod)

| | |
|---|---|
| Pod | `shorts-factory-2`, id `u8peu7pkfj7ara`, RTX 6000 Ada 48 GB, 16 vCPU, Secure Cloud US-IL-1, $0.84/hr (created 2026-09-23 because the original `wp74hgudj341g1`, now stopped, had no free GPU on its host) |
| Network volume | `nslmnoisc8`, 150 GB, mounted at `/workspace` (everything that matters lives here) |
| Repo | `/workspace/repo` (this repo) |
| Python | `/workspace/venvs/main` (pipeline, `shorts` CLI), `/workspace/venvs/tts` (Chatterbox), `/workspace/ACE-Step-1.5/.venv` (music) |
| Models | `HF_HOME=/workspace/hf` |
| Secrets | `/workspace/secrets.env`: ANTHROPIC_API_KEY (workspace-scoped), B2 keys, bucket `shortsPipeline` |

The container disk (including `/root` and apt packages such as ffmpeg) resets when the pod stops. Stop the pod when
a test session is done; say the hourly price before starting anything billable. A stopped pod can only restart on
its original host; if that host has no free GPU, create a new pod in US-IL-1 with the same image
(`runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04`, the venvs depend on its Python) and the same
network volume.

Session setup on the pod:
```bash
cd /workspace/repo && git pull
cd shorts-factory && bash scripts/runpod_bootstrap.sh   # idempotent; reinstalls ffmpeg after a restart
set -a; . /workspace/secrets.env; set +a
source /workspace/venvs/main/bin/activate && export HF_HOME=/workspace/hf
shorts doctor
```
Local tests (no GPU/API): `cd shorts-factory && python -m pytest -q`. Add `--mock` to any `shorts` command for a
free dry run.

## Where testing stands (2026-09-23)

First real run: `shorts run --date 2026-09-24 --channels history_en --count 3`, on the code *before* commit 5ae66e7.
- End to end works: trend brief, topics, script, Chatterbox voice, Whisper alignment, Z-Image-Turbo images,
  render, B2 delivery (`history/en/2026-09-24/…albert-alexander…mp4`), manifest, DB backup.
- Claude cost **$14.64 for 1 delivered video** (67 requests, 1.17M input / 308k output tokens, 104 web searches).
  Cause: about 9 full script loops for one video. 3 loops died on `max_tokens` (16k cap) and restarted from scratch,
  4 scripts were rejected by the quality gate after 3 rounds each, and web fact-checking ran on every draft.
- Jobs for 2026-09-24: 1 uploaded, 4 rejected, 1 still `planned` (7b4b4c).
- GPU timings: voice 14 scenes ≈ 99 s; alignment 12 s; images 14 × 14-23 s ≈ 6 min per video (per-image time rose
  from 14 s to 23 s during the batch; unexplained, worth checking for throttling); render ≈ 20 s.

Pushed after that run, **not yet tested on the GPU or against the real API**:
- `ANTHROPIC_WORKSPACE_ID` support and a clear error for unscoped keys; `shorts doctor` pings the Claude API.
- Claude calls stream with `llm.max_tokens: 64000`; a failed rewrite keeps the last draft (rejected, not failed).
- `shorts` finds `config/settings.yaml` from any folder.
- Fact-check runs only on drafts that pass lint + critic; per-step cost table after every run; `shorts status`
  prints why each script was rejected (jobs rejected before this change only have scores, not critic issues).

## Next steps

1. Setup above, then `shorts status --date 2026-09-24` for the rejection reasons.
2. `shorts run --date 2026-09-24 --channels history_en --count 3` on the new code (plans 1 job, scripts 2).
   Read the per-step cost table and the rejection reasons; tune thresholds or prompts from what they say.
3. Open decision (the user's call): cut Claude cost with Sonnet 5, the Batch API, or a local open-weight LLM on the
   GPU (full or hybrid: local drafts, Claude critic + fact-check). Proposed: an A/B of 3 videos each. Not built yet.
4. Scale to all four channels only once cost per delivered video is acceptable.
