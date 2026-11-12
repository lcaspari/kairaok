"""Bring the melody notes and the timed lyrics in line with each other.

The notes come from the audio (loudness, pitch, syllable onsets), the word times
from lyric alignment. Each has its own small errors. Since both describe the
same singing, they are reconciled here so that what you read and what you see
on the note highway agree:

  1. words are pulled onto the nearest note start (the audio knows the rhythm best)
  2. a note that runs across a word boundary is split there
  3. notes in passages without any lyrics are removed (echoes, ad-libs, bleed)
  4. the last note of a line may not ring on long after the line's last word
"""

from __future__ import annotations

import bisect
import copy

SNAP_WORD = 0.12        # a word start moves at most this far to meet a note start
SNAP_LINE_ONLY = 0.20   # same, when words were only estimated from line timing
LINE_PAD_BEFORE = 0.30  # notes may start this much before a line ...
LINE_PAD_AFTER = 0.35   # ... and end this much after it
MIN_NOTE = 0.07


def _snap_words(lines: list[dict], notes: list[dict], tol: float) -> None:
    starts = sorted(n["start"] for n in notes)
    if not starts:
        return
    prev_start = -1.0
    for line in lines:
        words = line["words"]
        for k, w in enumerate(words):
            i = bisect.bisect_left(starts, w["start"])
            best = None
            for j in (i - 1, i):
                if 0 <= j < len(starts) and abs(starts[j] - w["start"]) <= tol:
                    if best is None or abs(starts[j] - w["start"]) < abs(best - w["start"]):
                        best = starts[j]
            if best is not None and best > prev_start + 0.02:
                nxt = words[k + 1]["start"] if k + 1 < len(words) else None
                if nxt is None or best < nxt - 0.02:
                    w["start"] = best
            prev_start = w["start"]
        for k, w in enumerate(words):              # keep ends consistent
            if k + 1 < len(words):
                w["end"] = max(w["start"] + 0.02, min(w["end"], words[k + 1]["start"]))
                if words[k + 1]["start"] - w["end"] < 0.12:
                    w["end"] = words[k + 1]["start"]
            else:
                w["end"] = max(w["start"] + 0.05, w["end"])
        if words:
            line["start"] = words[0]["start"]
            line["end"] = max(line["start"] + 0.2, words[-1]["end"])


def _split_at_words(notes: list[dict], lines: list[dict]) -> list[dict]:
    cuts = sorted(w["start"] for line in lines for w in line["words"])
    out = []
    for n in notes:
        lo = bisect.bisect_right(cuts, n["start"] + 0.10)
        hi = bisect.bisect_left(cuts, n["end"] - 0.10)
        a = n["start"]
        for c in cuts[lo:hi]:
            if c - a < 0.10:
                continue
            out.append(dict(n, start=a, end=round(c - 0.02, 3)))
            a = c
        out.append(dict(n, start=a))
    return out


def _fit_to_lines(notes: list[dict], lines: list[dict]) -> list[dict]:
    spans = [(l["start"] - LINE_PAD_BEFORE, l["end"] + LINE_PAD_AFTER) for l in lines]
    merged: list[list[float]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    for n in notes:
        best = None
        for a, b in merged:
            ov = min(n["end"], b) - max(n["start"], a)
            if ov > 0 and (best is None or ov > best[0]):
                best = (ov, a, b)
        if best is None:
            continue                                # sung where there are no words
        _, a, b = best
        s, e = max(n["start"], a), min(n["end"], b)
        if e - s >= MIN_NOTE:
            out.append(dict(n, start=round(s, 3), end=round(e, 3)))
    return out


def reconcile(notes: list[dict], lines: list[dict], word_level: bool) -> tuple[list[dict], list[dict]]:
    """Return (notes, lines) adjusted to agree with each other. Inputs are not modified."""
    notes = [dict(n) for n in notes]
    lines = copy.deepcopy(lines)
    if not notes or not lines:
        return notes, lines

    _snap_words(lines, notes, SNAP_WORD if word_level else SNAP_LINE_ONLY)
    if word_level:
        # only trust the lyric timing enough to reshape notes when it is word-exact
        notes = _split_at_words(notes, lines)
        notes = _fit_to_lines(notes, lines)
    notes = [n for n in notes if n["end"] - n["start"] >= MIN_NOTE]
    notes.sort(key=lambda n: n["start"])
    return notes, lines
