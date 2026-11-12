"""Check that everything Kairaok needs is installed:  python -m kairaok.doctor"""

import importlib
import shutil
import sys


def check(label: str, fn) -> bool:
    try:
        detail = fn()
        print(f"  ok       {label}" + (f"  ({detail})" if detail else ""))
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  MISSING  {label}  ({exc.__class__.__name__}: {exc})")
        return False


def _version(name: str):
    return lambda: getattr(importlib.import_module(name), "__version__", "")


def _torch():
    import torch

    if torch.cuda.is_available():
        gpu = f"NVIDIA GPU: {torch.cuda.get_device_name(0)}"
    elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        gpu = "Apple GPU"
    else:
        gpu = "no GPU, using the CPU (works, just slower)"
    return f"{torch.__version__}, {gpu}"


def _binary(name: str):
    def fn():
        path = shutil.which(name)
        if not path:
            raise FileNotFoundError(f"{name} is not on the PATH")
        return path
    return fn


def _ytdlp():
    import yt_dlp

    return yt_dlp.version.__version__


def main() -> int:
    print(f"Kairaok check (Python {sys.version.split()[0]})")
    required = [
        check("ffmpeg", _binary("ffmpeg")),
        check("yt-dlp (YouTube download)", _ytdlp),
        check("deno (needed by yt-dlp for YouTube)", _binary("deno")),
        check("PyTorch", _torch),
        check("Demucs (vocal separation)", _version("demucs")),
        check("librosa (beat and melody analysis)", _version("librosa")),
        check("FastAPI (the app itself)", _version("fastapi")),
    ]
    optional = check("WhisperX (timing lyrics by ear)", _version("whisperx"))
    if not all(required):
        print("\nSomething required is missing. Run ./setup.sh again and read its output.")
        return 1
    if not optional:
        print("\nWhisperX is missing: songs without time-synced lyrics on LRCLIB will have no lyrics.")
    else:
        print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
