#!/bin/bash
# One-time setup: creates the "kairaok" conda environment and installs everything.
# Works on Apple Silicon Macs, Intel Macs and Linux (also Windows through WSL2).
set -e
cd "$(dirname "$0")"
. ./_env.sh

if [ "$PLATFORM" = "unsupported" ]; then
  echo "This system ($(uname -s)) is not supported directly. On Windows, run Kairaok inside WSL2 (see README)."
  exit 1
fi
if [ -z "$CONDA" ]; then
  echo "conda was not found. Install Miniforge (or Anaconda/Miniconda) first, then run this again."
  echo "  https://github.com/conda-forge/miniforge"
  exit 1
fi
echo "Using conda at: $CONDA"

case "$PLATFORM" in
  mac-arm)
    # Must be a native (arm64) environment, even if Anaconda itself is the Intel edition.
    # Intel environments on Apple Silicon get no ready-made PyTorch and audio packages.
    export CONDA_SUBDIR=osx-arm64
    WANT_ARCH=arm64
    echo "Computer: Mac with Apple Silicon (Demucs uses the Apple GPU)" ;;
  mac-intel)
    WANT_ARCH=x86_64
    echo "Computer: Intel Mac (everything runs on the CPU; a song takes longer)" ;;
  linux)
    WANT_ARCH="$(uname -m)"
    echo "Computer: Linux ($WANT_ARCH)" ;;
esac

if use_env; then
  HAVE_ARCH="$("$PY" -c 'import platform; print(platform.machine())' 2>/dev/null || echo broken)"
  if [ "$HAVE_ARCH" != "$WANT_ARCH" ]; then
    echo "The existing '$ENV_NAME' environment is $HAVE_ARCH but this computer needs $WANT_ARCH. Rebuilding it."
    "$CONDA" env remove -y -n "$ENV_NAME"
  else
    echo "Environment '$ENV_NAME' already exists ($HAVE_ARCH), updating it."
  fi
fi

if ! use_env; then
  echo "Creating environment '$ENV_NAME' (Python 3.11 + ffmpeg, $WANT_ARCH)..."
  "$CONDA" create -y -n "$ENV_NAME" --override-channels -c conda-forge python=3.11 "ffmpeg>=6,<8"
  use_env || { echo "The environment was created but could not be found. Output of 'conda env list':"; "$CONDA" env list; exit 1; }
  [ "$PLATFORM" = "mac-arm" ] && echo "subdir: osx-arm64" > "$ENV_PREFIX/.condarc"
fi

HAVE_ARCH="$("$PY" -c 'import platform; print(platform.machine())')"
echo "Python: $("$PY" --version 2>&1) at $PY ($HAVE_ARCH)"
if [ "$HAVE_ARCH" != "$WANT_ARCH" ]; then
  echo "This Python is $HAVE_ARCH, expected $WANT_ARCH. Stopping here."
  exit 1
fi

"$PY" -m pip install --upgrade pip

PIP_EXTRA=()
case "$PLATFORM" in
  mac-intel)
    PIP_EXTRA=(-c constraints/intel-mac.txt) ;;
  linux)
    # Without an NVIDIA graphics card, take the much smaller CPU-only PyTorch
    # (the default one bundles about 3 GB of CUDA libraries).
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
      echo "NVIDIA GPU found: installing PyTorch with CUDA."
    else
      echo "No NVIDIA GPU found: installing the CPU-only PyTorch."
      "$PY" -m pip install --prefer-binary torch torchaudio --index-url https://download.pytorch.org/whl/cpu
    fi ;;
esac

echo "Installing Kairaok's packages (PyTorch, Demucs, WhisperX, ...). This downloads about 2-3 GB."
if ! "$PY" -m pip install --prefer-binary "${PIP_EXTRA[@]}" -r requirements.txt whisperx; then
  echo
  echo "WhisperX could not be installed together with the rest. Installing without it."
  echo "Kairaok still works with time-synced lyrics from LRCLIB; timing by ear will be unavailable."
  "$PY" -m pip install --prefer-binary "${PIP_EXTRA[@]}" -r requirements.txt
fi

echo
"$PY" -m kairaok.doctor
echo
echo "Setup finished. Start Kairaok with:  ./run.sh"
