# RunPod guide (engineer)

## Which GPU

| GPU | VRAM | NVENC | Fits | Use it when |
|---|---|---|---|---|
| RTX 4090 | 24 GB | yes | TTS, Whisper, Z-Image-Turbo | Budget. Tight for 20B image models; Wan animation only with heavy offload (slow) |
| **L40S** | 48 GB | yes | everything except fast Wan A14B | **Recommended default.** Room for bigger image models (Qwen-Image), hardware video encoding |
| A100 / H100 80 GB | 80 GB | **no** | everything incl. Wan 2.2 A14B without offload | You turn on hook animation (`animate.enabled`). Pick a pod with 16+ vCPU: renders fall back to CPU x264 |

The pipeline only needs one GPU. GPU stages run one after another, each loading its model once for the whole batch,
so VRAM needs are set by the single biggest model, not the sum.

## Capacity and cost (estimates: measure your own)

Rough per-video numbers for a 45 s Short with 13 scenes on an L40S. `shorts make` logs real per-stage timings, so run it once on your pod and redo the math.

| Stage | Where | Per video | 42 videos/day |
|---|---|---|---|
| Trend brief | Claude web search, once per channel | ~1 min | ~5 min |
| Script: write, fact-check, critique, rewrite | Claude API, 6 in parallel | 2-4 min wall | ~15-25 min |
| Voice (Chatterbox) | GPU | 20-40 s | ~15-30 min |
| Word timings (Whisper large-v3) | GPU | ~5 s | ~3 min |
| Images (Z-Image-Turbo, 13 × 1088x1920) | GPU | 40-80 s | ~30-55 min |
| Render (captions, motion, mix) | CPU, 3 in parallel (NVENC if present) | 20-40 s | ~10-15 min |
| Hook animation (Wan 2.2 A14B, optional) | GPU | 4-8 min on H100 | +3-5 h |

**Without animation: ~1.5-2 GPU-hours per day** for 42 videos. **With hook animation: an H100 for ~5-7 hours.**

Monthly ballpark at 42/day (check current RunPod and Anthropic pricing):
- GPU: L40S ~2 h/day ≈ $50-70/mo. H100 with animation ≈ $400-600/mo.
- Network volume (150 GB) ≈ $10/mo.
- Claude API: roughly **$0.30-0.70 per published video** on `claude-opus-5` (writer, critic, web fact-check and rewrites) plus a few cents per channel per day for the trend search, about $400-850/mo at 42/day. It's usually the biggest line item. Levers, in order: `web_search_max_uses: 3`; `max_rewrites: 1`; `llm.model: claude-sonnet-5` (about 60% cheaper; measure the quality change on 20 scripts first). Every run prints actual token usage and an estimated cost.

## Setup (≈ 30 minutes)

1. **Network volume** (Storage → New Network Volume): 150 GB, in a datacenter that has your GPU type. Models, the database, videos and secrets live here and survive pod restarts.
2. **Deploy a pod**: GPU as above, template **RunPod PyTorch** (CUDA 12.x, Python 3.11), attach the volume at `/workspace`, container disk 30 GB. Expose HTTP port 8888 (Jupyter) so you can open review pages and videos in the browser.
3. In the pod's terminal (web terminal or SSH):
   ```bash
   cd /workspace
   git clone <your repo url> repo && cd repo/shorts-factory
   bash scripts/runpod_bootstrap.sh          # ffmpeg, both venvs, fonts, database
   cat > /workspace/secrets.env <<'EOF'
   ANTHROPIC_API_KEY=sk-ant-...
   B2_KEY_ID=...                             # Backblaze application key (see "Backblaze B2" below)
   B2_APPLICATION_KEY=...
   HF_TOKEN=hf_...                           # only needed for gated models like FLUX.1-dev
   # ELEVENLABS_API_KEY=...                  # only if tts.backend: elevenlabs
   EOF
   chmod 600 /workspace/secrets.env
   set -a; . /workspace/secrets.env; set +a
   source /workspace/venvs/main/bin/activate && export HF_HOME=/workspace/hf
   python scripts/prefetch_models.py          # ~40-60 GB of weights, once
   shorts doctor
   ```
4. Add your assets: `assets/voices/<channel>.wav`, `assets/music/<mood>/…`, `assets/sfx/whoosh/…` (read the READMEs there: licensing matters).
   For music, `shorts music` generates the whole library on the GPU (ACE-Step 1.5; its weights download on the first run
   and the command logs real per-track timings). Try `shorts music --moods epic --count 3` and listen before a full run.
5. First videos: `shorts make --channel history_en --topic "The Great Emu War of 1932"`, then open `data/jobs/<today>/…/final.mp4` in Jupyter. Tune `visual_style`, voice `exaggeration` and caption settings until you love it.
6. Set up Backblaze B2 (below) and check `shorts doctor` shows `storage (b2)` green.

## Backblaze B2

1. B2 → Buckets → **Create a Bucket**: private. Copy its **S3 endpoint** from the bucket card (e.g. `https://s3.us-west-004.backblazeb2.com`).
2. B2 → Application Keys → **Add a New Application Key**: restrict it to that bucket, Read and Write. Save the keyID and applicationKey into `/workspace/secrets.env` as `B2_KEY_ID` / `B2_APPLICATION_KEY`. The key is shown only once.
3. In `config/settings.yaml` → `storage`: set `bucket` and `endpoint` (or `B2_BUCKET` / `B2_S3_ENDPOINT` in `secrets.env`).
4. `shorts doctor`, then `shorts make --channel history_en --topic "…" --upload` and check the file appears under `history/en/<date>/`.

Every video is size-verified after upload. Each niche/language/date folder gets a `_manifest.csv` (opens in Excel or Google Sheets) with
posting order, title, description and a 7-day download link per video. Grab a whole day with the B2 web UI, `rclone` or
`b2 sync`. Storage need is about 35 MB per video (about 45 GB/month at 42/day); add a B2 lifecycle rule if you want old
days removed automatically. After each verified upload, local media is deleted (`storage.cleanup_local: media`),
so the network volume doesn't fill up.

The same code works with any S3-compatible store (Cloudflare R2, Wasabi, AWS S3): change `endpoint` and the keys.

## Daily automation

A full day's batch takes 1.5-2 hours, so don't keep the GPU running 24/7. Pick one:

**A. Manual (simplest to start):** start the pod from the console each day, run `bash scripts/daily_run.sh`, stop the pod. About 5 minutes of your time.

**B. Self-stopping pod:** run `STOP_POD_WHEN_DONE=1 bash scripts/daily_run.sh` (e.g. via `nohup … &`). The pod stops itself once the batch is in B2. Start it on a timer from anywhere:
```bash
# RunPod REST API v1. RunPod has announced v1 stops serving on 2026-11-15; switch to its successor
# (or `runpodctl start pod <id>`) when RunPod publishes the migration path.
curl -X POST "https://rest.runpod.io/v1/pods/$POD_ID/start" -H "Authorization: Bearer $RUNPOD_API_KEY"
```
Then have the pod run `daily_run.sh` on boot, either with the custom image below (`RUN_DAILY=1`) or by adding the command to the template's start command.
Caveat: a stopped pod gets its GPU back only if one is free in that datacenter. If starts fail, create a fresh pod on the same network volume instead; nothing is lost.

**C. Custom image:** build `Dockerfile` (`docker build -t you/shorts-factory . && docker push …`), make a RunPod template from it with env `RUN_DAILY=1`, and point it at the network volume. Each start produces tomorrow's batch and stops the pod; without `RUN_DAILY` it boots into the normal SSH/Jupyter environment.

Everything lands in B2 before the pod stops, so the pod can be off while you post.

## Operating

```bash
shorts status --date tomorrow            # jobs per state per channel; failed jobs show their error
shorts review --date tomorrow            # HTML page with every video, script and critic scores
shorts approve --date tomorrow           # when publish.require_approval is on
shorts run --date tomorrow --stages upload   # (re)deliver ready videos to B2
shorts run --date tomorrow --stages render,qc   # re-run a stage (stages only pick up jobs in their input state)
shorts report --channel history_en       # what's winning
```
Everything is resumable: if a run dies halfway, run the same command again.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `CUDA out of memory` in the image stage | `images.cpu_offload: true`, or a smaller model / resolution (e.g. 896x1600) |
| Whisper: `libcudnn…` not found | `pip install nvidia-cudnn-cu12==9.*` in the main venv, or `align.compute_type: int8_float16` |
| Chatterbox import errors | Rebuild the TTS venv: `rm -rf /workspace/venvs/tts && bash scripts/runpod_bootstrap.sh` |
| `ffmpeg lacks libass` / no captions | Use the distro ffmpeg (`apt-get install ffmpeg`); static builds without libass can't burn captions |
| Renders slow on H100/A100 | These GPUs have no NVENC; use a pod with more vCPUs or raise `render.workers` |
| `voice too long` rejections | Lower `target_seconds` by 3-5 s in the channel YAML, or `tts.speed: 1.1` |
| Scripts rejected a lot | Look at `data/jobs/…/script.rejected.json` for critic notes; relax `min_overall_score` slightly or improve the channel brief |
| `storage (b2)` red in doctor | Check `B2_KEY_ID` / `B2_APPLICATION_KEY` are exported, the key is allowed on that bucket, and `endpoint` matches the bucket's region |
| `Unsupported header 'x-amz-sdk-checksum-algorithm'` | Already handled in `B2Store`. If you see it, you are calling boto3 directly without the `when_required` checksum config |
| Videos stay `ready` | Delivery failed or approval is on: `shorts status`, then `shorts run --date … --stages upload` |
