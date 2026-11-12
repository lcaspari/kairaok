"""Settings. Every value can be overridden with an environment variable."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

# Where prepared songs are stored (one folder per song).
LIBRARY = Path(os.environ.get("KAIRAOK_LIBRARY", ROOT / "library"))

# Demucs model used for vocal / instrumental separation.
# "htdemucs" is the fast default, "htdemucs_ft" is slower and a bit cleaner.
DEMUCS_MODEL = os.environ.get("KAIRAOK_DEMUCS_MODEL", "htdemucs")

# Whisper model used when lyrics have to be transcribed or matched by ear.
# tiny < base < small < medium < large-v3 (slower, more accurate).
WHISPER_MODEL = os.environ.get("KAIRAOK_WHISPER_MODEL", "small")

# Refine line-timed lyrics to word-level timing with WhisperX (1 = on, 0 = off).
WORD_LEVEL = os.environ.get("KAIRAOK_WORD_LEVEL", "1") != "0"

# A synced lyrics file is trusted only if its track length is within this many
# seconds of the downloaded audio. Otherwise the text is re-timed by ear.
SYNC_DURATION_TOLERANCE = float(os.environ.get("KAIRAOK_SYNC_TOLERANCE", "3.0"))

HOST = os.environ.get("KAIRAOK_HOST", "127.0.0.1")
PORT = int(os.environ.get("KAIRAOK_PORT", "8642"))

SAMPLE_RATE = 44100
USER_AGENT = "Kairaok/0.1 (personal karaoke app)"
