#!/usr/bin/env bash
# One-time, idempotent setup on a RunPod GPU pod (PyTorch template + network volume at /workspace).
# Re-run safely after every pod (re)start: it only installs what is missing.
#
#   cd /workspace/<repo>/shorts-factory && bash scripts/runpod_bootstrap.sh
set -euo pipefail

WS="${WORKSPACE:-/workspace}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> system packages"
if ! command -v ffmpeg >/dev/null || ! command -v sqlite3 >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg sqlite3 fonts-dejavu-core >/dev/null
fi
ffmpeg -hide_banner -filters 2>/dev/null | grep -q " subtitles " || { echo "ffmpeg lacks libass"; exit 1; }

echo "==> main venv (reuses the image's CUDA torch)"
if [ ! -x "$WS/venvs/main/bin/python" ]; then
  python3 -m venv --system-site-packages "$WS/venvs/main"
fi
"$WS/venvs/main/bin/pip" install -q -U pip
"$WS/venvs/main/bin/pip" install -q -e ".[gpu,dev]"

echo "==> isolated TTS venv (chatterbox pins torch 2.6)"
if [ ! -x "$WS/venvs/tts/bin/python" ]; then
  python3 -m venv "$WS/venvs/tts"
fi
"$WS/venvs/tts/bin/pip" install -q -U pip
"$WS/venvs/tts/bin/pip" install -q -r requirements/tts-chatterbox.txt

echo "==> AI music generator (ACE-Step 1.5 pins torch 2.10)"
bash scripts/setup_music.sh || echo "    music generator setup failed (only \`shorts music\` needs it): re-run scripts/setup_music.sh"

echo "==> fonts, folders, database"
bash scripts/fetch_fonts.sh
export HF_HOME="$WS/hf"
"$WS/venvs/main/bin/shorts" init

cat <<EOF

Done. Add to ~/.bashrc (or $WS/secrets.env, which scripts/daily_run.sh sources):

  export HF_HOME=$WS/hf
  export ANTHROPIC_API_KEY=...        # console.anthropic.com
  export B2_KEY_ID=...                # Backblaze application key for the delivery bucket
  export B2_APPLICATION_KEY=...
  export HF_TOKEN=...                 # only for gated models (e.g. FLUX.1-dev)
  source $WS/venvs/main/bin/activate

Then:
  python scripts/prefetch_models.py   # download weights once to the network volume
  shorts doctor                       # everything green?
  shorts music                        # AI background music for every mood folder (one-time)
  shorts make --channel history_en --topic "The Great Emu War of 1932"
EOF
