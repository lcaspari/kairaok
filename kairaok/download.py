"""Get the audio of a song: from a YouTube link or from a local file."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import config

ProgressFn = Callable[[float], None]

_NOISE = re.compile(
    r"""[\(\[\{【][^\)\]\}】]*(
        official|video|audio|lyric|lyrics|visuali[sz]er|hd|hq|4k|mv|m/v|
        remaster|remastered|live|explicit|clean|version|edit|clip|karaoke|
        offizielles|musikvideo
    )[^\)\]\}】]*[\)\]\}】]""",
    re.IGNORECASE | re.VERBOSE,
)
_FEAT = re.compile(r"\s*[\(\[]?\b(feat\.?|ft\.?|featuring)\b.*$", re.IGNORECASE)


def clean_title(text: str) -> str:
    text = _NOISE.sub("", text or "")
    text = re.sub(r"\s*\|.*$", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -–—|\"'")


def guess_artist_title(info: dict) -> tuple[str, str]:
    """Best guess for (artist, title) from yt-dlp metadata."""
    artist = (info.get("artist") or "").strip()
    track = (info.get("track") or "").strip()
    if artist and track:
        return artist.split(",")[0].strip(), clean_title(track)

    title = clean_title(info.get("title") or "")
    parts = re.split(r"\s+[-–—]\s+|\s*[–—]\s*", title, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return _FEAT.sub("", parts[0]).strip(), _FEAT.sub("", parts[1]).strip(" \"'")

    uploader = info.get("channel") or info.get("uploader") or ""
    uploader = re.sub(r"\s*-\s*Topic$|VEVO$", "", uploader).strip()
    return uploader, _FEAT.sub("", title).strip()


def to_wav(src: Path, dst: Path) -> None:
    """Convert anything ffmpeg can read to 44.1 kHz stereo 16-bit WAV."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
        "-vn", "-ar", str(config.SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s16le", str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg was not found. Run setup.sh again.") from exc
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"Could not read the audio file: {proc.stderr.strip()[:300]}")


def download_youtube(url: str, out_dir: Path, progress: Optional[ProgressFn] = None) -> tuple[Path, dict]:
    """Download the best audio stream and return (wav_path, info)."""
    import yt_dlp

    def hook(d: dict) -> None:
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info.get("entries"):
                info = info["entries"][0]
            src = Path(ydl.prepare_filename(info))
    except Exception as exc:  # yt-dlp raises many types
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
        if "not a bot" in msg or "Sign in to confirm" in msg:
            raise RuntimeError(
                "YouTube refused this download from your current network (it asks for a sign-in "
                "to prove you are not a bot). Try again later or on another network, try a "
                "different upload of the song, or add the song as an audio file."
            ) from exc
        raise RuntimeError(
            f"Download failed: {msg[:300]} "
            "(If YouTube changed something, run update.sh to get the newest yt-dlp.)"
        ) from exc

    if not src.exists():
        candidates = sorted(out_dir.glob("source.*"))
        if not candidates:
            raise RuntimeError("Download finished but no audio file was written.")
        src = candidates[0]

    wav = out_dir / "original.wav"
    to_wav(src, wav)
    src.unlink(missing_ok=True)

    slim = {
        k: info.get(k)
        for k in ("id", "title", "artist", "track", "album", "channel", "uploader",
                  "duration", "thumbnail", "webpage_url")
    }
    return wav, slim
