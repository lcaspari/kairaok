#!/bin/bash
# YouTube changes often. If downloads start failing, run this to get the newest yt-dlp.
cd "$(dirname "$0")"
. ./_env.sh
use_env || { echo "Kairaok is not set up yet. Run ./setup.sh first."; exit 1; }
PIP_EXTRA=()
[ "$PLATFORM" = "mac-intel" ] && PIP_EXTRA=(-c constraints/intel-mac.txt)
"$PY" -m pip install -U "${PIP_EXTRA[@]}" "yt-dlp[default,deno]"
