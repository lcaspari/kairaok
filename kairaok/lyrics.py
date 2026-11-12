"""Lyrics: look up time-synced lyrics on LRCLIB, parse LRC, shape lines and words.

A line is a dict: {"start": s, "end": s, "text": str, "words": [{"start", "end", "text"}]}
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from . import config

LRCLIB = "https://lrclib.net/api"

_TS = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_WORD_TS = re.compile(r"<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>")
_META = re.compile(r"^\[([a-zA-Z#]+):(.*)\]$")
_SECTION = re.compile(
    r"^[\[\(]\s*(verse|chorus|bridge|intro|outro|hook|pre-?chorus|refrain|interlude|"
    r"instrumental|solo|strophe|part|post-?chorus|break)\b[^\]\)]*[\]\)]$",
    re.IGNORECASE,
)


def _seconds(m: str, s: str, frac: Optional[str]) -> float:
    f = int(frac) / (10 ** len(frac)) if frac else 0.0
    return int(m) * 60 + int(s) + f


def is_lrc(text: str) -> bool:
    return len(_TS.findall(text or "")) >= 3


def norm(text: str) -> str:
    """Lowercase, strip accents and punctuation: for fuzzy comparisons."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]|_", "", text.lower()).strip()


# --------------------------------------------------------------------------- LRC


def parse_lrc(text: str) -> list[dict]:
    """Return sorted entries {"start", "text", "words"?}. Empty text marks a pause."""
    offset = 0.0
    entries: list[dict] = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        meta = _META.match(raw)
        if meta and not _TS.match(raw):
            if meta.group(1).lower() == "offset":
                try:
                    offset = float(meta.group(2)) / 1000.0
                except ValueError:
                    pass
            continue
        stamps = []
        rest = raw
        while True:
            m = _TS.match(rest)
            if not m:
                break
            stamps.append(_seconds(*m.groups()))
            rest = rest[m.end():].lstrip()
        if not stamps:
            continue

        words = None
        if _WORD_TS.search(rest):  # enhanced LRC with per-word stamps
            words = []
            pieces = _WORD_TS.split(rest)
            # pieces: [before, m, s, f, text, m, s, f, text, ...]
            for i in range(1, len(pieces) - 3 + 1, 4):
                t = _seconds(pieces[i], pieces[i + 1], pieces[i + 2])
                w = pieces[i + 3].strip()
                if w:
                    words.append({"start": t, "text": w})
            rest = " ".join(w["text"] for w in words) if words else _WORD_TS.sub("", rest)

        for t in stamps:
            entry = {"start": max(0.0, t - offset), "text": rest.strip()}
            if words and len(stamps) == 1:
                entry["words"] = [
                    {"start": max(0.0, w["start"] - offset), "text": w["text"]} for w in words
                ]
            entries.append(entry)
    entries.sort(key=lambda e: e["start"])
    return entries


def lines_from_lrc(text: str, duration: float) -> list[dict]:
    entries = parse_lrc(text)
    lines: list[dict] = []
    for i, e in enumerate(entries):
        if not e["text"] or _SECTION.match(e["text"]):
            continue
        nxt = entries[i + 1]["start"] if i + 1 < len(entries) else duration
        end = max(e["start"] + 0.3, min(nxt, e["start"] + 15.0))
        line = {"start": e["start"], "end": end, "text": e["text"], "words": []}
        if e.get("words"):
            ws = e["words"]
            for j, w in enumerate(ws):
                w_end = ws[j + 1]["start"] if j + 1 < len(ws) else end
                line["words"].append({"start": w["start"], "end": w_end, "text": w["text"]})
            line["word_timed"] = True
        else:
            spread_words(line)
        lines.append(line)
    return lines


def plain_to_texts(text: str) -> list[str]:
    """Plain lyrics -> list of sung lines (drops blank lines and [Chorus] markers)."""
    out = []
    for raw in (text or "").splitlines():
        raw = _TS.sub("", raw).strip()
        if not raw or _SECTION.match(raw):
            continue
        out.append(raw)
    return out


# ------------------------------------------------------------------ line shaping


def spread_words(line: dict) -> dict:
    """Give each word a time slot inside the line, weighted by word length."""
    tokens = line["text"].split()
    if not tokens:
        line["words"] = []
        return line
    weights = [len(t) + 1.5 for t in tokens]
    total = sum(weights)
    span = max(0.05, line["end"] - line["start"])
    t = line["start"]
    words = []
    for tok, w in zip(tokens, weights):
        d = span * w / total
        words.append({"start": round(t, 3), "end": round(t + d, 3), "text": tok})
        t += d
    line["words"] = words
    return line


def trim_line_ends(lines: list[dict], notes: list[dict]) -> list[dict]:
    """Line-timed lyrics only know when a line starts. Use the sung melody to
    find where each line really ends, then spread the words over that span."""
    for i, line in enumerate(lines):
        nxt = lines[i + 1]["start"] if i + 1 < len(lines) else line["end"]
        nxt = max(nxt, line["start"] + 0.3)
        inside = [n for n in notes if n["end"] > line["start"] - 0.2 and n["start"] < nxt - 0.05]
        n_words = max(1, len(line["text"].split()))
        if inside:
            end = inside[0]["end"]
            for prev, cur in zip(inside, inside[1:]):
                if cur["start"] - prev["end"] > 1.5 and prev["end"] - line["start"] > 0.25 * n_words:
                    break
                end = cur["end"]
            end = min(nxt, end + 0.1)
        else:
            end = min(nxt, line["start"] + 0.45 * n_words + 1.0)
        line["end"] = round(max(line["start"] + 0.3, end), 3)
        spread_words(line)
    return lines


def finalize(lines: list[dict], duration: float) -> list[dict]:
    """Sort, clamp and round so the player can rely on clean data."""
    clean = []
    for line in sorted(lines, key=lambda l: l["start"]):
        if not line.get("text", "").strip():
            continue
        start = min(max(0.0, float(line["start"])), duration)
        end = min(max(start + 0.2, float(line["end"])), max(duration, start + 0.2))
        words = []
        t = start
        for w in line.get("words") or []:
            ws = min(max(t, float(w["start"])), end)
            we = min(max(ws + 0.02, float(w["end"])), max(end, ws + 0.02))
            words.append({"start": round(ws, 3), "end": round(we, 3), "text": w["text"]})
            t = ws
        if not words:
            words = spread_words({"start": start, "end": end, "text": line["text"]})["words"]
        clean.append({"start": round(start, 3), "end": round(end, 3),
                      "text": line["text"].strip(), "words": words})
    return clean


# ----------------------------------------------------------------------- LRCLIB


@dataclass
class LyricsHit:
    synced: Optional[str] = None      # LRC text
    plain: Optional[str] = None
    trusted: bool = False             # synced timing matches this audio's length
    duration_diff: Optional[float] = None
    track: str = ""
    artist: str = ""
    instrumental: bool = False


def _similar(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return max(0.85, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


def _get(path: str, params: dict):
    import requests

    r = requests.get(f"{LRCLIB}/{path}", params=params, timeout=15,
                     headers={"User-Agent": config.USER_AGENT})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def find_lyrics(artist: str, title: str, duration: float) -> Optional[LyricsHit]:
    """Search LRCLIB. Prefers synced lyrics whose track length matches the audio."""
    candidates: list[dict] = []
    seen = set()

    def add(items):
        for it in items or []:
            if isinstance(it, dict) and it.get("id") not in seen:
                seen.add(it.get("id"))
                candidates.append(it)

    queries = []
    if artist and title:
        queries.append(("get", {"artist_name": artist, "track_name": title,
                                "duration": int(round(duration))}))
        queries.append(("search", {"track_name": title, "artist_name": artist}))
    if title:
        queries.append(("search", {"q": f"{artist} {title}".strip()}))

    errors = 0
    for path, params in queries:
        try:
            res = _get(path, params)
        except Exception:
            errors += 1
            continue
        add([res] if isinstance(res, dict) else res)
    if errors == len(queries) and queries:
        raise RuntimeError("LRCLIB could not be reached.")

    scored = []
    for c in candidates:
        t_sim = _similar(c.get("trackName", ""), title)
        a_sim = _similar(c.get("artistName", ""), artist) if artist else 0.7
        if t_sim < 0.6 or a_sim < 0.5:
            continue
        diff = abs(float(c.get("duration") or 0) - duration)
        scored.append((c, t_sim + a_sim, diff))
    if not scored:
        return None

    synced = [s for s in scored if s[0].get("syncedLyrics")]
    plain = [s for s in scored if s[0].get("plainLyrics")]
    hit = LyricsHit()
    if synced:
        c, _, diff = min(synced, key=lambda s: (s[2] > config.SYNC_DURATION_TOLERANCE, s[2], -s[1]))
        hit.synced = c["syncedLyrics"]
        hit.plain = c.get("plainLyrics")
        hit.trusted = diff <= config.SYNC_DURATION_TOLERANCE
        hit.duration_diff = round(diff, 1)
        hit.track, hit.artist = c.get("trackName", ""), c.get("artistName", "")
    if not hit.plain and plain:
        c, _, diff = min(plain, key=lambda s: (s[2], -s[1]))
        hit.plain = c["plainLyrics"]
        hit.track = hit.track or c.get("trackName", "")
        hit.artist = hit.artist or c.get("artistName", "")
    if not hit.synced and not hit.plain:
        hit.instrumental = any(s[0].get("instrumental") for s in scored)
        if not hit.instrumental:
            return None
    return hit
