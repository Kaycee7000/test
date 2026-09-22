#!/usr/bin/env bash
# Download the open-licence (SIL OFL) caption fonts into assets/fonts.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p assets/fonts
base="https://raw.githubusercontent.com/google/fonts/main/ofl"
fetch() {
  local url="$1" out="assets/fonts/$2"
  [ -s "$out" ] && { echo "have $2"; return; }
  curl -fsSL --retry 3 "$url" -o "$out" && echo "got $2"
}
fetch "$base/anton/Anton-Regular.ttf" Anton-Regular.ttf
fetch "$base/bebasneue/BebasNeue-Regular.ttf" BebasNeue-Regular.ttf
fetch "$base/montserrat/Montserrat%5Bwght%5D.ttf" Montserrat-Variable.ttf
