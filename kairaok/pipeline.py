"""Turns a link or an audio file into a ready-to-sing song folder.

library/<id>/
    meta.json           title, artist, status, best score
    original.wav        the downloaded audio
    vocals.flac         isolated singer
    instrumental.flac   backing track
    song.json           beats, melody notes, timed lyrics
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

from . import align, analysis, config, download, lyrics, sync

_lock = threading.Lock()


# ------------------------------------------------------------------ song folder


def song_dir(song_id: str) -> Path:
    return config.LIBRARY / song_id


def read_meta(song_id: str) -> Optional[dict]:
    p = song_dir(song_id) / "meta.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_meta(song_id: str, **changes) -> dict:
    with _lock:
        if not song_dir(song_id).is_dir():      # song was removed meanwhile
            return {"id": song_id}
        meta = read_meta(song_id) or {"id": song_id}
        meta.update(changes)
        p = song_dir(song_id) / "meta.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        return meta


def list_songs() -> list[dict]:
    config.LIBRARY.mkdir(parents=True, exist_ok=True)
    songs = [m for d in config.LIBRARY.iterdir() if d.is_dir() and (m := read_meta(d.name))]
    return sorted(songs, key=lambda m: m.get("created", 0), reverse=True)


def create_song(url: str = "", artist: str = "", title: str = "", pasted_lyrics: str = "",
                filename: str = "") -> dict:
    song_id = uuid.uuid4().hex[:10]
    song_dir(song_id).mkdir(parents=True, exist_ok=True)
    return write_meta(
        song_id, url=url, artist=artist.strip(), title=title.strip() or filename or "New song",
        user_artist=artist.strip(), user_title=title.strip(), pasted_lyrics=pasted_lyrics,
        created=time.time(), status="queued", step="queued", step_label="Waiting in line",
        progress=0.0, error=None, warnings=[], best_score=None, thumbnail=None,
    )


def delete_song(song_id: str) -> None:
    shutil.rmtree(song_dir(song_id), ignore_errors=True)


def mark_interrupted() -> None:
    """Songs that were mid-processing when the app was closed cannot continue."""
    for m in list_songs():
        if m.get("status") in ("queued", "processing"):
            write_meta(m["id"], status="error",
                       error="Preparation was interrupted when the app closed. Remove the song and add it again.")


# -------------------------------------------------------------------- pipeline


def _reconcile(song: dict) -> None:
    """Fit notes and lyric timing to each other. The untouched versions are kept
    (notes_raw, lyrics.raw_lines) so this can be redone when either side changes."""
    ly = song["lyrics"]
    raw_lines = ly.get("raw_lines") or ly["lines"]
    raw_notes = song.get("notes_raw") or song["notes"]
    notes, lines = sync.reconcile(raw_notes, raw_lines, bool(ly.get("word_level")))
    song["notes_raw"], ly["raw_lines"] = raw_notes, raw_lines
    song["notes"] = notes
    ly["lines"] = lyrics.finalize(lines, song["duration"])


class _Status:
    def __init__(self, song_id: str):
        self.id = song_id
        self.lo, self.hi = 0.0, 0.0
        self._last = 0.0

    def step(self, key: str, label: str, lo: float, hi: float) -> None:
        self.lo, self.hi = lo, hi
        write_meta(self.id, status="processing", step=key, step_label=label, progress=lo)

    def progress(self, fraction: float) -> None:
        now = time.time()
        if now - self._last < 0.4 and fraction < 1.0:
            return
        self._last = now
        write_meta(self.id, progress=round(self.lo + (self.hi - self.lo) * max(0.0, min(1.0, fraction)), 3))


def process_song(song_id: str, upload: Optional[Path] = None) -> None:
    """Run the whole preparation. Called from a background worker."""
    st = _Status(song_id)
    d = song_dir(song_id)
    try:
        meta = read_meta(song_id)
        if meta is None:
            return

        # 1 ---- audio
        wav = d / "original.wav"
        if upload is not None:
            st.step("download", "Reading your audio file", 0.0, 0.08)
            download.to_wav(upload, wav)
            upload.unlink(missing_ok=True)
            info = {}
        else:
            st.step("download", "Downloading the song", 0.0, 0.08)
            _, info = download.download_youtube(meta["url"], d, st.progress)

        import soundfile as sf

        duration = float(sf.info(str(wav)).duration)
        artist, title = meta.get("user_artist", ""), meta.get("user_title", "")
        if info and not (artist and title):
            g_artist, g_title = download.guess_artist_title(info)
            artist, title = artist or g_artist, title or g_title
        title = title or meta.get("title") or "Untitled"
        write_meta(song_id, artist=artist, title=title, duration=round(duration, 2),
                   thumbnail=info.get("thumbnail"), youtube_title=info.get("title"))

        # 2 ---- stems
        st.step("separate", "Separating the singer from the band", 0.08, 0.50)
        from . import separate

        vocals, instrumental, device = separate.separate(wav, d)
        write_meta(song_id, device=device)

        # 3 ---- beats
        st.step("beats", "Finding the beat", 0.50, 0.56)
        beat_info = analysis.analyze_beats(instrumental)

        # 4 ---- melody
        st.step("melody", "Tracing the melody", 0.56, 0.74)
        melody = analysis.analyze_melody(vocals, beat_info["beats"], st.progress)

        # 5 ---- lyrics
        st.step("lyrics", "Finding and timing the lyrics", 0.74, 0.98)
        lyr = build_lyrics(d, artist, title, duration, meta.get("pasted_lyrics", ""),
                           melody["notes"], st)

        song = {
            "id": song_id, "title": title, "artist": artist, "duration": round(duration, 2),
            **beat_info, **melody, "lyrics": lyr,
        }
        _reconcile(song)
        (d / "song.json").write_text(json.dumps(song, ensure_ascii=False), encoding="utf-8")
        write_meta(song_id, status="ready", step="ready", step_label="Ready to sing", progress=1.0,
                   bpm=beat_info["bpm"], lyrics_source=lyr["source"], warnings=lyr["warnings"],
                   error=None)
    except Exception as exc:
        traceback.print_exc()
        write_meta(song_id, status="error", error=str(exc) or exc.__class__.__name__)


def redo_lyrics(song_id: str, artist: str, title: str, pasted: str, mode: str) -> None:
    """Fetch / time the lyrics again for a prepared song (stems are reused)."""
    st = _Status(song_id)
    d = song_dir(song_id)
    try:
        song = json.loads((d / "song.json").read_text(encoding="utf-8"))
        st.step("lyrics", "Finding and timing the lyrics", 0.05, 0.98)
        lyr = build_lyrics(d, artist, title, song["duration"], pasted,
                           song.get("notes_raw") or song["notes"], st, mode)
        song.update({"lyrics": lyr, "artist": artist, "title": title})
        _reconcile(song)
        (d / "song.json").write_text(json.dumps(song, ensure_ascii=False), encoding="utf-8")
        write_meta(song_id, status="ready", step="ready", step_label="Ready to sing", progress=1.0,
                   artist=artist, title=title, lyrics_source=lyr["source"],
                   warnings=lyr["warnings"], error=None)
    except Exception as exc:
        traceback.print_exc()
        # the song itself is still singable
        write_meta(song_id, status="ready", step="ready", progress=1.0,
                   warnings=[f"Updating the lyrics failed: {exc}"])


def redo_melody(song_id: str) -> None:
    """Trace the melody again for a prepared song (stems, beats and lyrics are kept)."""
    st = _Status(song_id)
    d = song_dir(song_id)
    try:
        song = json.loads((d / "song.json").read_text(encoding="utf-8"))
        st.step("melody", "Tracing the melody", 0.05, 0.98)
        song.update(analysis.analyze_melody(d / "vocals.flac", song["beats"], st.progress))
        song["notes_raw"] = song["notes"]
        _reconcile(song)
        (d / "song.json").write_text(json.dumps(song, ensure_ascii=False), encoding="utf-8")
    except Exception:
        traceback.print_exc()
    write_meta(song_id, status="ready", step="ready", step_label="Ready to sing", progress=1.0)


# ---------------------------------------------------------------------- lyrics

SOURCE_LABELS = {
    "lrclib-synced": "Time-synced lyrics from LRCLIB",
    "lrclib-ear": "Lyrics from LRCLIB, timed by ear with WhisperX",
    "lrclib-untimed-fallback": "Synced lyrics from LRCLIB (a different version of the song)",
    "pasted-synced": "Your synced lyrics",
    "pasted-ear": "Your lyrics, timed by ear with WhisperX",
    "whisperx": "Transcribed by ear with WhisperX",
    "none": "No lyrics",
}


def build_lyrics(d: Path, artist: str, title: str, duration: float, pasted: str,
                 notes: list[dict], st: _Status, mode: str = "auto") -> dict:
    """Decide where the lyrics come from and get them timed.

    mode: "auto"        synced lyrics if they fit, otherwise time by ear
          "ear"         always re-time the lyric text against the vocals
          "transcribe"  ignore lyric databases, write down what is sung
    """
    vocals = str(d / "vocals.flac")
    warnings: list[str] = []
    lines: list[dict] = []
    texts: Optional[list[str]] = None      # text without usable timing
    fallback_lrc: Optional[str] = None     # synced lyrics of doubtful fit
    source = "none"
    word_level = False
    language = None
    has_whisperx = align.available()

    pasted = (pasted or "").strip()
    if mode == "transcribe":
        pass
    elif pasted:
        if lyrics.is_lrc(pasted) and mode != "ear":
            lines, source = lyrics.lines_from_lrc(pasted, duration), "pasted-synced"
        else:
            texts, source = lyrics.plain_to_texts(pasted), "pasted-ear"
    else:
        hit = None
        try:
            hit = lyrics.find_lyrics(artist, title, duration)
        except Exception as exc:
            warnings.append(f"Lyrics lookup failed ({exc}).")
        if hit and hit.synced and hit.trusted and mode != "ear":
            lines, source = lyrics.lines_from_lrc(hit.synced, duration), "lrclib-synced"
        elif hit and (hit.plain or hit.synced):
            texts = lyrics.plain_to_texts(hit.plain or "") or \
                [l["text"] for l in lyrics.lines_from_lrc(hit.synced or "", duration)]
            source, fallback_lrc = "lrclib-ear", hit.synced
        elif hit and hit.instrumental:
            warnings.append("LRCLIB lists this track as instrumental.")
        elif not warnings:
            warnings.append(f"No lyrics found on LRCLIB for “{artist} – {title}”.")
    st.progress(0.1)

    sub = lambda lo, hi: (lambda f: st.progress(lo + (hi - lo) * f))  # noqa: E731

    if lines:                                           # line-timed lyrics
        if sum(1 for l in lines if l.get("word_timed")) >= max(1, len(lines) // 2):
            word_level = True
        else:
            lyrics.trim_line_ends(lines, notes)
            if config.WORD_LEVEL and has_whisperx:
                try:
                    lines, refined = align.refine_lines(vocals, lines, progress=sub(0.15, 1.0))
                    word_level = refined >= max(1, len(lines) // 2)
                except Exception as exc:
                    warnings.append(f"Word-level timing skipped ({exc}).")
    else:                                               # needs timing by ear
        if has_whisperx and (texts or mode == "transcribe" or source == "none"):
            try:
                segments, heard, language = align.transcribe(vocals, progress=sub(0.15, 1.0))
                if texts:
                    lines, ratio = align.map_lyrics(texts, heard)
                    word_level = True
                    if ratio < 0.3:
                        lines = []
                        warnings.append("The lyric text did not match what is sung in this recording.")
                else:
                    lines, source, word_level = align.segments_to_lines(segments), "whisperx", True
                    if lines:
                        warnings.append("Lyrics were written down by ear and may contain mistakes.")
                    else:
                        source = "none"
                if not lines and texts:
                    if fallback_lrc:
                        lines = lyrics.lines_from_lrc(fallback_lrc, duration)
                        lyrics.trim_line_ends(lines, notes)
                        source, word_level = "lrclib-untimed-fallback", False
                    else:
                        lines, source = align.segments_to_lines(segments), "whisperx"
                        if lines:
                            warnings.append("Showing what WhisperX heard instead.")
            except Exception as exc:
                warnings.append(f"WhisperX failed ({exc}).")
        if not lines and not has_whisperx:
            if fallback_lrc:
                lines = lyrics.lines_from_lrc(fallback_lrc, duration)
                lyrics.trim_line_ends(lines, notes)
                source = "lrclib-untimed-fallback"
            elif texts or source == "none":
                warnings.append("WhisperX is not installed, so lyrics could not be timed by ear.")
        if source == "lrclib-untimed-fallback":
            warnings.append("These lyrics were timed for a different version of the song. "
                            "Use the lyrics offset if they run early or late.")
        if not lines:
            source = "none"

    if lines and not language:
        language = align.detect_language(" ".join(l["text"] for l in lines))
    return {
        "language": language,
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "word_level": bool(word_level and lines),
        "warnings": warnings,
        "lines": lyrics.finalize(lines, duration),
    }
