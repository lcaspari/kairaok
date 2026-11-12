# Shared by setup.sh, run.sh and update.sh: finds conda, the "kairaok" environment
# and what kind of computer this is.
ENV_NAME=kairaok

CONDA="$(command -v conda || true)"
for c in "$HOME/miniforge3/bin/conda" "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" \
         "/opt/anaconda3/bin/conda" "/opt/miniconda3/bin/conda" "/opt/miniforge3/bin/conda" \
         "/opt/homebrew/Caskroom/miniconda/base/bin/conda" "/opt/homebrew/Caskroom/miniforge/base/bin/conda" \
         "/usr/local/Caskroom/miniconda/base/bin/conda" "/usr/local/Caskroom/miniforge/base/bin/conda"; do
  [ -z "$CONDA" ] && [ -x "$c" ] && CONDA="$c"
done

# PLATFORM is one of: mac-arm (Apple Silicon), mac-intel, linux, unsupported.
# On a Mac the hardware is checked, not the terminal: an Apple Silicon Mac stays
# mac-arm even when the terminal or conda itself runs as an Intel program.
case "$(uname -s)" in
  Darwin)
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then PLATFORM=mac-arm; else PLATFORM=mac-intel; fi ;;
  Linux)
    PLATFORM=linux ;;
  *)
    PLATFORM=unsupported ;;
esac
APPLE_SILICON=0
[ "$PLATFORM" = "mac-arm" ] && APPLE_SILICON=1

env_prefix() {   # prints the folder of the kairaok environment, or nothing
  [ -n "$CONDA" ] && "$CONDA" env list 2>/dev/null | awk -v n="$ENV_NAME" '$1==n {print $NF}'
}

use_env() {      # puts the environment's programs (python, ffmpeg, deno) first on the PATH
  ENV_PREFIX="$(env_prefix)"
  [ -n "$ENV_PREFIX" ] && [ -x "$ENV_PREFIX/bin/python" ] || return 1
  PY="$ENV_PREFIX/bin/python"
  export PATH="$ENV_PREFIX/bin:$PATH"
}

open_url() {     # opens a page in the default browser, wherever possible
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v wslview >/dev/null 2>&1; then wslview "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1"
  else echo "Open $1 in your browser."
  fi
}
