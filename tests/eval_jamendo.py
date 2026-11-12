"""Measure note detection against hand-annotated word timings (JamendoLyrics dataset).

Downloads a few Creative-Commons songs with word-by-word timing, separates the
vocals with Kairaok's own Demucs step, detects notes, and reports how well the
note rhythm agrees with where the words really are.

    python tests/eval_jamendo.py            # the default seven songs
    python tests/eval_jamendo.py 3          # only the first three

Numbers to watch (higher is better unless noted):
  onsets_found   share of annotated word starts that have a note start within 0.1 s
  on_a_word      share of note starts that are within 0.1 s of a word start
  outside        share of note time lying outside any lyric line (lower is better)
  line_cover     share of lyric-line time covered by notes
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from kairaok import analysis, download, sync  # noqa: E402

BASE = "https://raw.githubusercontent.com/f90/jamendolyrics/master/"
SONGS = [
    "Wordsmith_-_The_Statement", "JASON_MILLER_-_CROWD_PLEASER",          # hip-hop
    "Rxbyn_-_Bad_Side", "HILA_-_Give_Me_the_Same", "Quentin_Hannappe_-_Keep_On",
    "Avercage_-_Embers", "Moon_I_Mean_-_Wrong_Concept",
]
DATA = HERE / "_jamendo"


def fetch(rel: str, dst: Path) -> Path:
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(BASE + urllib.parse.quote(rel), dst)
    return dst


def truth(stem: str):
    rows = list(csv.DictReader(open(fetch(f"annotations/words/{stem}.csv", DATA / f"{stem}.words.csv"))))
    lines, cur = [], []
    for r in rows:
        cur.append({"start": float(r["word_start"]), "end": float(r["word_end"]), "text": "x"})
        if r["line_end"] != "nan":
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return [{"start": ws[0]["start"], "end": ws[-1]["end"], "text": " ".join("x" for _ in ws), "words": ws}
            for ws in lines]


def metrics(notes, lines, tol=0.10):
    words = np.array([w["start"] for l in lines for w in l["words"]])
    starts = np.array([n["start"] for n in notes])
    total = sum(n["end"] - n["start"] for n in notes) or 1e-9
    inside = sum(max(0, min(l["end"] + 0.15, n["end"]) - max(l["start"] - 0.15, n["start"]))
                 for l in lines for n in notes)
    line_time = sum(l["end"] - l["start"] for l in lines)
    return {
        "onsets_found": float(np.mean([np.min(np.abs(starts - w)) <= tol for w in words])),
        "on_a_word": float(np.mean([np.min(np.abs(words - s)) <= tol for s in starts])),
        "outside": 1 - min(1.0, inside / total),
        "line_cover": min(1.0, inside / line_time),
    }


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(SONGS)
    rows = []
    for stem in SONGS[:n]:
        d = DATA / stem
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "vocals.flac").exists():
            mp3 = fetch(f"mp3/{stem}.mp3", d / "song.mp3")
            download.to_wav(mp3, d / "original.wav")
            from kairaok import separate
            separate.separate(d / "original.wav", d)
        beats = analysis.analyze_beats(d / "instrumental.flac")
        notes = analysis.analyze_melody(d / "vocals.flac", beats["beats"])["notes"]
        lines = truth(stem)
        fitted, _ = sync.reconcile(notes, lines, True)
        raw, fit = metrics(notes, lines), metrics(fitted, lines)
        rows.append((raw, fit))
        free = sum(1 for x in notes if x.get("free")) / max(1, len(notes))
        print(f"{stem[:30]:30s} notes {len(notes):4d} ({free:.0%} spoken)  "
              + "  ".join(f"{k} {raw[k]:.2f}>{fit[k]:.2f}" for k in raw), flush=True)
    print("\nmean (from audio alone > after fitting to the word timing)")
    for k in rows[0][0]:
        print(f"  {k:13s} {np.mean([r[0][k] for r in rows]):.2f} > {np.mean([r[1][k] for r in rows]):.2f}")
    json.dump([{"raw": r[0], "fitted": r[1]} for r in rows], open(DATA / "results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
