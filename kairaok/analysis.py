"""Music analysis: beats from the instrumental, melody notes from the vocals."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import numpy as np

ProgressFn = Callable[[float], None]

SR = 22050
HOP = 256                      # 11.6 ms per frame
FMIN, FMAX = 65.0, 1100.0      # C2 .. C6, covers every singing voice
TAIL_RATIO = 0.15              # quieter than this share of the recent peak = fading tail
ONSET_DELTA = 0.07             # sensitivity of syllable onset detection (lower = more onsets)
ONSET_DELTA_SUNG = 0.13        # same, inside a held sung note
STEADY_SPREAD = 0.6            # semitones: pitch wobble below this means "sung", not spoken


# ------------------------------------------------------------------------ beats


def analyze_beats(instrumental_path: Path) -> dict:
    """Tempo, beat times and (estimated) bar starts of the backing track."""
    import librosa

    y, sr = librosa.load(str(instrumental_path), sr=SR, mono=True)
    hop = 512
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop)
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=hop)
    tempo = float(np.atleast_1d(tempo)[0])

    # Bar starts: assume 4/4 and pick the beat phase that carries the most low-end
    # energy (kick drum / bass usually land on the "one").
    downbeats: list[float] = []
    if len(frames) >= 8:
        S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
        low = S[: int(150 * 2048 / sr) + 1].sum(axis=0)
        low_onset = np.maximum(0.0, np.diff(low, prepend=low[0]))
        idx = np.clip(frames, 0, len(low_onset) - 1)
        strength = np.array([low_onset[max(0, i - 2): i + 3].max() for i in idx])
        phase = int(np.argmax([strength[p::4].mean() for p in range(4)]))
        downbeats = [float(t) for t in beats[phase::4]]

    return {
        "bpm": round(tempo, 1),
        "beats": [round(float(t), 3) for t in beats],
        "downbeats": [round(t, 3) for t in downbeats],
    }


# ----------------------------------------------------------------------- melody


def _fill_small_gaps(mask: np.ndarray, max_gap: int, min_run: int) -> np.ndarray:
    """Close short holes in a boolean mask, then drop runs that are too short."""
    m = mask.copy()
    n = len(m)
    i = 0
    while i < n:                                    # close holes
        if not m[i]:
            j = i
            while j < n and not m[j]:
                j += 1
            if 0 < i and j < n and j - i <= max_gap:
                m[i:j] = True
            i = j
        else:
            i += 1
    i = 0
    while i < n:                                    # drop blips
        if m[i]:
            j = i
            while j < n and m[j]:
                j += 1
            if j - i < min_run:
                m[i:j] = False
            i = j
        else:
            i += 1
    return m


def track_pitch(vocals_path: Path, progress: Optional[ProgressFn] = None):
    """Analyse the vocal stem frame by frame.

    Returns (times, midi, guess, active, onsets):
      midi    pitch where the voice clearly sings a tone, NaN elsewhere
      guess   best pitch estimate for every analysed frame, even unclear ones
      active  True wherever the singer is audible at all (sung, spoken or rapped)
      onsets  frame numbers where a new syllable starts (the rhythm of the vocal)

    How long a note lasts is decided by `active` (loudness), which pitch it has
    by `midi`. Keeping the two apart stops notes from being cut short whenever
    the pitch becomes unclear, as it does on consonants and in half-spoken lines.
    """
    import librosa

    y, sr = librosa.load(str(vocals_path), sr=SR, mono=True)
    n_frames = 1 + len(y) // HOP
    midi = np.full(n_frames, np.nan)
    guess = np.full(n_frames, np.nan)
    active = np.zeros(n_frames, dtype=bool)
    times = np.arange(n_frames) * HOP / sr
    if not len(y) or np.max(np.abs(y)) < 1e-4:
        return times, midi, guess, active, np.array([], dtype=int)

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0][:n_frames]
    ref = np.percentile(rms, 95)
    frames_per = lambda sec: max(1, int(round(sec * sr / HOP)))  # noqa: E731
    loud = rms > max(ref * 0.06, 1e-4)             # about -24 dB below the loud parts
    # Audible = above a floor AND not just the fading tail of what came before.
    # A reverb or delay tail keeps sounding after the singer has stopped; it is
    # recognised by having dropped far below the loudest moment of the last 0.6 s.
    w = frames_per(0.6)
    recent_peak = np.array([rms[max(0, k - w): k + 1].max() for k in range(len(rms))])
    audible = (rms > max(ref * 0.10, 2e-4)) & (rms > recent_peak * TAIL_RATIO)
    active[: len(rms)] = _fill_small_gaps(audible, frames_per(0.05), frames_per(0.06))

    # Syllable onsets: sudden rises in the vocal's spectrum.
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, units="frames",
                                        backtrack=False, delta=ONSET_DELTA, wait=frames_per(0.07))
    onsets = np.array([o for o in onsets if o < n_frames and active[o:o + frames_per(0.05)].any()], dtype=int)
    # On a held, clearly sung tone only a pronounced onset counts as a new note,
    # otherwise vibrato would chop long notes into pieces. Encoded as negative = weak.
    strong = set(int(o) for o in librosa.onset.onset_detect(
        onset_envelope=env, sr=sr, hop_length=HOP, units="frames", backtrack=False,
        delta=ONSET_DELTA_SUNG, wait=frames_per(0.07)))
    onsets = np.array([o if int(o) in strong else -o for o in onsets], dtype=int)

    # group loud frames into regions for the pitch tracker, with a little padding
    regions = []
    pad, bridge = 8, 30
    i = 0
    while i < len(loud):
        if not loud[i]:
            i += 1
            continue
        j = i
        last = i
        while j < len(loud) and (loud[j] or j - last <= bridge):
            if loud[j]:
                last = j
            j += 1
        regions.append((max(0, i - pad), min(len(loud), last + pad + 1)))
        i = j

    total = sum(b - a for a, b in regions) or 1
    done = 0
    for a, b in regions:
        seg = y[a * HOP: (b - 1) * HOP + 2048]
        if len(seg) < 4096:
            done += b - a
            continue
        f0, voiced, prob = librosa.pyin(
            seg, fmin=FMIN, fmax=FMAX, sr=sr, frame_length=2048, hop_length=HOP,
            center=True, fill_na=None,
        )
        with np.errstate(invalid="ignore", divide="ignore"):
            m = librosa.hz_to_midi(f0)
        n = min(len(m), b - a)
        g = m[:n].copy()
        strict = m[:n].copy()
        strict[~voiced[:n] | (prob[:n] < 0.35)] = np.nan
        quiet = rms[a: a + n] < ref * 0.03          # bleed from the backing track
        strict[quiet] = np.nan
        g[quiet] = np.nan
        midi[a: a + n] = strict
        guess[a: a + n] = g
        done += b - a
        if progress:
            progress(done / total)
    return times, midi, guess, active, onsets


def _median_smooth(x: np.ndarray, k: int = 5) -> np.ndarray:
    out = x.copy()
    h = k // 2
    for i in range(len(x)):
        w = x[max(0, i - h): i + h + 1]
        w = w[~np.isnan(w)]
        if np.isnan(x[i]) or len(w) == 0:
            continue
        out[i] = np.median(w)
    return out


def _pitched_notes(times: np.ndarray, midi: np.ndarray, lo: int, hi: int,
                   min_dur: float) -> list[dict]:
    """Notes from the clearly sung frames between frame lo and hi."""
    dt = float(times[1] - times[0]) if len(times) > 1 else HOP / SR
    max_gap = max(1, int(round(0.045 / dt)))        # bridge consonant-sized dropouts
    notes: list[dict] = []
    cur: list[float] = []
    start_i = lo
    gap = 0

    def close(end_i: int) -> None:
        nonlocal cur
        if cur and (end_i - start_i) * dt >= min_dur:
            notes.append({"a": start_i, "b": end_i, "midi": int(round(float(np.median(cur))))})
        cur = []

    for i in range(lo, hi):
        m = midi[i]
        if np.isnan(m):
            if cur:
                gap += 1
                if gap > max_gap:
                    close(i - gap + 1)
            continue
        if cur:
            centre = float(np.median(cur[-12:]))
            if abs(m - centre) > 0.75:              # pitch moved on: new note
                close(i - gap)
                start_i = i
        else:
            start_i = i
        gap = 0
        cur.append(float(m))
    close(hi - gap)
    return notes


def notes_from_pitch(times: np.ndarray, midi: np.ndarray, guess: Optional[np.ndarray] = None,
                     active: Optional[np.ndarray] = None, onsets: Optional[np.ndarray] = None,
                     min_dur: float = 0.09) -> list[dict]:
    """Turn the analysis into notes: {"start", "end", "midi", "free"?}.

    Each stretch where the singer is audible is covered completely:
      - clearly sung notes are stretched to meet each other, so unclear moments
        (consonants, breathy or half-spoken syllables) no longer leave holes;
      - stretches with no clear tone at all (rap, speech) become "free" notes,
        which are scored on timing only.
    """
    midi = _median_smooth(midi, 5)
    n = len(midi)
    dt = float(times[1] - times[0]) if n > 1 else HOP / SR
    if active is None:
        active = ~np.isnan(midi)
    if guess is None:
        guess = midi
    fr = lambda sec: max(1, int(round(sec / dt)))   # noqa: E731
    max_stretch = fr(0.40)                          # longest unclear part a sung note may absorb

    def free_note(a: int, b: int, near: Optional[int]) -> Optional[dict]:
        if (b - a) * dt < 0.12:
            return None
        g = guess[a:b]
        g = g[~np.isnan(g)]
        pitch = int(round(float(np.median(g)))) if len(g) >= 3 else near
        if pitch is None:
            return None
        if near is not None and abs(pitch - near) > 7:   # keep it near the melody
            pitch += 12 * int(round((near - pitch) / 12))
        return {"a": a, "b": b, "midi": pitch, "free": True}

    out: list[dict] = []
    i = 0
    while i < n:
        if not active[i]:
            i += 1
            continue
        j = i
        while j < n and active[j]:
            j += 1
        # active stretch [i, j)
        notes = _pitched_notes(times, midi, i, j, min_dur)
        near = out[-1]["midi"] if out else None
        if not notes:
            f = free_note(i, j, near)
            if f:
                out.append(f)
            i = j
            continue

        filled: list[dict] = []
        cursor = i
        for k, nt in enumerate(notes):
            lead = nt["a"] - cursor
            if lead > max_stretch:                  # long unclear part before this note
                f = free_note(cursor, nt["a"] - fr(0.03), nt["midi"])
                if f:
                    filled.append(f)
                else:
                    nt["a"] = max(cursor, nt["a"] - fr(0.12))
            elif lead > 0:
                if filled and not filled[-1].get("free"):
                    filled[-1]["b"] = nt["a"]       # previous note holds until this one starts
                else:
                    nt["a"] = cursor                # onset consonant belongs to this note
            filled.append(nt)
            cursor = nt["b"]
        tail = j - cursor
        if tail > max_stretch:
            f = free_note(cursor + fr(0.03), j, filled[-1]["midi"])
            if f:
                filled.append(f)
        elif tail > 0:
            filled[-1]["b"] = j
        out.extend(filled)
        i = j

    # Rhythm: start a new note wherever a new syllable starts, also on a held pitch.
    if onsets is not None and len(onsets):
        edge = fr(0.08)
        split: list[dict] = []
        for nt in out:
            # weak onsets (stored negative) only split spoken notes
            cuts = [abs(int(o)) for o in onsets
                    if nt["a"] + edge < abs(o) < nt["b"] - edge and (o > 0 or nt.get("free"))]
            a = nt["a"]
            for o in cuts + [None]:
                b = nt["b"] if o is None else o
                piece = dict(nt, a=a, b=b if o is None else b - fr(0.025))
                if not nt.get("free"):
                    seg = midi[a:b]
                    seg = seg[~np.isnan(seg)]
                    if len(seg) >= 3:
                        piece["midi"] = int(round(float(np.median(seg))))
                split.append(piece)
                a = b
        out = split

    # A "spoken" piece whose pitch estimate holds steady is sung after all (soft or
    # breathy singing is often too unclear for the strict tracker): give it its tone.
    for nt in out:
        if nt.get("free"):
            g = guess[nt["a"]:nt["b"]]
            g = g[~np.isnan(g)]
            if len(g) >= max(5, 0.6 * (nt["b"] - nt["a"])) and (nt["b"] - nt["a"]) * dt >= 0.12 \
                    and np.percentile(g, 80) - np.percentile(g, 20) < STEADY_SPREAD:
                nt["midi"] = int(round(float(np.median(g))))
                nt.pop("free")

    notes_out = []
    for nt in out:
        d = {"start": float(times[nt["a"]]), "end": float(times[min(nt["b"], n - 1)]),
             "midi": int(nt["midi"])}
        if nt.get("free"):
            d["free"] = True
        if d["end"] - d["start"] >= 0.06:
            notes_out.append(d)

    # merge neighbours that are the same pitch with almost no pause between them
    merged: list[dict] = []
    for nt in notes_out:
        prev = merged[-1] if merged else None
        if prev and not nt.get("free") and not prev.get("free") \
                and nt["midi"] == prev["midi"] and nt["start"] - prev["end"] < 0.004:
            prev["end"] = nt["end"]
        else:
            merged.append(dict(nt))

    # drop wild octave jumps of very short notes (tracking glitches): give them the neighbour's pitch
    for k in range(1, len(merged) - 1):
        cur, prev, nxt = merged[k], merged[k - 1], merged[k + 1]
        if cur["end"] - cur["start"] < 0.14 and not cur.get("free"):
            if abs(cur["midi"] - prev["midi"]) >= 9 and abs(cur["midi"] - nxt["midi"]) >= 9:
                cur["midi"] = prev["midi"]
    return merged


def snap_to_beats(notes: list[dict], beats: list[float], division: int = 4,
                  tolerance: float = 0.025) -> list[dict]:
    """Pull note edges onto the beat grid (16th notes) when they are already close.

    This puts the melody in line with the backing track without bending notes
    that were sung deliberately off the grid.
    """
    if len(beats) < 2:
        return notes
    b = np.asarray(beats, dtype=float)
    grid = []
    for t0, t1 in zip(b[:-1], b[1:]):
        grid.extend(t0 + (t1 - t0) * k / division for k in range(division))
    grid.append(float(b[-1]))
    grid = np.asarray(grid)
    step = float(np.median(np.diff(b))) / division
    tol = min(tolerance, step * 0.3)

    def snap(t: float) -> float:
        i = int(np.searchsorted(grid, t))
        near = [grid[j] for j in (i - 1, i) if 0 <= j < len(grid)]
        g = min(near, key=lambda x: abs(x - t))
        return float(g) if abs(g - t) <= tol else t

    out = []
    for n in notes:
        s, e = snap(n["start"]), snap(n["end"])
        if e - s < 0.06:
            s, e = n["start"], n["end"]
        if out and s < out[-1]["end"]:
            s = out[-1]["end"]
        if e - s < 0.05:
            continue
        item = {"start": round(s, 3), "end": round(e, 3), "midi": n["midi"]}
        if n.get("free"):
            item["free"] = True
        out.append(item)
    return out


def fix_octaves(notes: list[dict], window: float = 3.0, max_leap: float = 6.5) -> int:
    """Undo octave slips of the pitch tracker (in place). Returns how many notes moved.

    A voice rarely leaps more than a sixth away from what it has been singing in
    the last few seconds, but a pitch tracker easily lands an octave too high or
    too low. Every note is compared with the notes around it and moved by whole
    octaves until it sits within reach of them.
    """
    if len(notes) < 3:
        return 0
    starts = np.array([n["start"] for n in notes])
    durs = np.array([max(0.03, n["end"] - n["start"]) for n in notes])
    # spoken notes carry only a rough pitch guess: let the sung ones set the context
    weight = durs * np.array([0.25 if n.get("free") else 1.0 for n in notes])
    pitch = np.array([float(n["midi"]) for n in notes])

    def context(i: int, vals: np.ndarray) -> Optional[float]:
        lo = np.searchsorted(starts, starts[i] - window)
        hi = np.searchsorted(starts, starts[i] + window)
        idx = [j for j in range(lo, hi) if j != i]
        if len(idx) < 2:
            return None
        order = np.argsort(vals[idx])
        v, w = vals[idx][order], weight[idx][order]
        return float(v[np.searchsorted(np.cumsum(w), w.sum() / 2)])

    moved = 0
    for _ in range(2):                              # second pass settles the neighbours
        for i in range(len(notes)):
            ctx = context(i, pitch)
            if ctx is None:
                continue
            p = pitch[i]
            while p - ctx > max_leap:
                p -= 12
            while ctx - p > max_leap:
                p += 12
            if p != pitch[i]:
                pitch[i] = p
                moved += 1
    for n, p in zip(notes, pitch):
        n["midi"] = int(p)
    return moved


def analyze_melody(vocals_path: Path, beats: list[float],
                   progress: Optional[ProgressFn] = None) -> dict:
    times, midi, guess, active, onsets = track_pitch(vocals_path, progress)
    notes = snap_to_beats(notes_from_pitch(times, midi, guess, active, onsets), beats)
    fix_octaves(notes)
    if notes:
        sung = [n for n in notes if not n.get("free")] or notes
        pitches = np.array([n["midi"] for n in sung])
        weights = np.array([n["end"] - n["start"] for n in sung])
        order = np.argsort(pitches)
        cum = np.cumsum(weights[order]) / weights.sum()
        lo = int(pitches[order][np.searchsorted(cum, 0.03)])
        hi = int(pitches[order][min(len(order) - 1, np.searchsorted(cum, 0.97))])
        if hi - lo > 19:                             # show at most about an octave and a half
            mid = int(pitches[order][np.searchsorted(cum, 0.5)])
            lo, hi = max(lo, mid - 10), min(hi, mid + 9)
        for n in notes:                              # bring the few strays into the shown range
            while n["midi"] > hi and n["midi"] - 12 >= lo:
                n["midi"] -= 12
            while n["midi"] < lo and n["midi"] + 12 <= hi:
                n["midi"] += 12
    else:
        lo, hi = 55, 72
    return {"notes": notes, "range": [lo, hi]}
