#!/usr/bin/env bash
# Isolated venv for the AI music generator (ACE-Step 1.5). It pins torch 2.10, so it never shares a venv
# with the image stack. Idempotent. Model weights download on the first `shorts music` run.
#
#   bash scripts/setup_music.sh
set -euo pipefail

WS="${WORKSPACE:-/workspace}"
DIR="${ACESTEP_DIR:-$WS/ACE-Step-1.5}"   # must match music.acestep_dir in config/settings.yaml
REF="${ACESTEP_REF:-v0.1.8}"             # pinned release; test a newer tag before bumping

if [ ! -d "$DIR/.git" ]; then
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$REF" \
    https://github.com/ace-step/ACE-Step-1.5.git "$DIR"
elif [ "$(git -C "$DIR" describe --tags --exact-match 2>/dev/null || true)" != "$REF" ]; then
  git -C "$DIR" fetch --quiet --depth 1 origin tag "$REF"
  git -C "$DIR" -c advice.detachedHead=false checkout --quiet "$REF"
fi

UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  "$WS/venvs/main/bin/pip" install -q uv
  UV="$WS/venvs/main/bin/uv"
fi

echo "    installing ACE-Step and its own torch (a few GB, several minutes)"
cd "$DIR"
UV_LINK_MODE=copy "$UV" sync
"$DIR/.venv/bin/python" -c "import acestep.inference" && echo "    ACE-Step ready: $DIR/.venv/bin/python"
