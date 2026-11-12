"""WhisperX: time lyrics against the isolated vocals.

Three uses:
  refine_lines()   - lines already have rough times (LRC) -> exact word times
  transcribe()     - no timing at all -> words with times, by ear
  map_lyrics()     - place known lyrics text onto a transcript's word times
"""

from __future__ import annotations

import gc
from difflib import SequenceMatcher
from typing import Callable, Optional

from . import config
from .lyrics import norm, spread_words

ProgressFn = Callable[[float], None]
DEVICE = "cpu"  # WhisperX's backend (CTranslate2) has no Apple GPU support


def available() -> bool:
    try:
        import whisperx  # noqa: F401
        return True
    except Exception:
        return False


def detect_language(text: str, default: str = "en") -> str:
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        return detect(text).split("-")[0]
    except Exception:
        return default


def _free(*objs) -> None:
    for o in objs:
        del o
    gc.collect()


# ------------------------------------------------------------------ transcribe


def transcribe(vocals_path: str, language: Optional[str] = None,
               progress: Optional[ProgressFn] = None) -> tuple[list[dict], list[dict], str]:
    """Return (segments, words, language). Words: {"start", "end", "text"}."""
    import whisperx

    audio = whisperx.load_audio(vocals_path)
    model = whisperx.load_model(config.WHISPER_MODEL, DEVICE, compute_type="int8",
                                language=language)
    result = model.transcribe(audio, batch_size=4)
    language = result.get("language") or language or "en"
    _free(model)
    if progress:
        progress(0.6)

    segments = result["segments"]
    try:
        align_model, meta = whisperx.load_align_model(language_code=language, device=DEVICE)
        aligned = whisperx.align(segments, align_model, meta, audio, DEVICE,
                                 return_char_alignments=False)
        _free(align_model)
        segments = aligned["segments"]
    except Exception:
        pass  # no alignment model for this language: keep segment-level times
    if progress:
        progress(1.0)

    out_segments, words = [], []
    for seg in segments:
        seg_words = []
        for w in seg.get("words") or []:
            if "start" in w and "end" in w and w.get("word", "").strip():
                seg_words.append({"start": float(w["start"]), "end": float(w["end"]),
                                  "text": w["word"].strip()})
        if not seg_words and seg.get("text", "").strip():
            line = spread_words({"start": float(seg["start"]), "end": float(seg["end"]),
                                 "text": seg["text"].strip()})
            seg_words = line["words"]
        if seg_words:
            out_segments.append({"start": seg_words[0]["start"], "end": seg_words[-1]["end"],
                                 "text": " ".join(w["text"] for w in seg_words),
                                 "words": seg_words})
            words.extend(seg_words)
    return out_segments, words, language


def segments_to_lines(segments: list[dict], max_words: int = 9, max_gap: float = 1.2) -> list[dict]:
    """Break transcript segments into singable lines."""
    lines = []
    for seg in segments:
        cur: list[dict] = []
        for w in seg["words"]:
            if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > max_gap):
                lines.append(cur)
                cur = []
            cur.append(w)
        if cur:
            if len(cur) <= 2 and lines and cur[0]["start"] - lines[-1][-1]["end"] < 0.6 \
                    and len(lines[-1]) < max_words + 2:
                lines[-1].extend(cur)
            else:
                lines.append(cur)
    return [{"start": ws[0]["start"], "end": ws[-1]["end"],
             "text": " ".join(w["text"] for w in ws), "words": ws} for ws in lines]


# ----------------------------------------------------------- how well was it sung


def text_match(lyric_texts: list[str], heard_words: list[str]) -> dict:
    """Share of the lyric words that were recognisably sung, in order."""
    expected = [norm(t) for text in lyric_texts for t in text.split()]
    expected = [t for t in expected if t]
    heard = [h for h in (norm(w) for w in heard_words) if h]
    if not expected:
        return {"score": None, "expected": 0, "heard": len(heard)}
    sm = SequenceMatcher(None, expected, heard, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    return {"score": round(matched / len(expected), 3), "expected": len(expected), "heard": len(heard)}


def score_recording(audio_path: str, lyric_texts: list[str], language: Optional[str]) -> dict:
    """Transcribe a recording of the singer and compare it with the lyrics."""
    import whisperx

    audio = whisperx.load_audio(audio_path)
    model = whisperx.load_model(config.WHISPER_MODEL, DEVICE, compute_type="int8", language=language)
    result = model.transcribe(audio, batch_size=4)
    _free(model)
    heard = [w for seg in result["segments"] for w in seg.get("text", "").split()]
    return text_match(lyric_texts, heard)


# ------------------------------------------------------------- known lyrics text


def map_lyrics(texts: list[str], heard: list[dict]) -> tuple[list[dict], float]:
    """Place known lyric lines onto the times of transcribed words.

    Returns (lines, match_ratio). Words the transcript missed are interpolated
    between their matched neighbours.
    """
    tokens: list[tuple[int, str]] = []
    for li, text in enumerate(texts):
        for tok in text.split():
            tokens.append((li, tok))
    if not tokens or not heard:
        return [], 0.0

    a = [norm(t) or t.lower() for _, t in tokens]
    b = [norm(w["text"]) or w["text"].lower() for w in heard]
    sm = SequenceMatcher(None, a, b, autojunk=False)
    times: list[Optional[tuple[float, float]]] = [None] * len(tokens)
    matched = 0
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            w = heard[blk.b + k]
            times[blk.a + k] = (w["start"], w["end"])
            matched += 1
    ratio = matched / len(tokens)

    # interpolate the gaps
    known = [i for i, t in enumerate(times) if t is not None]
    if not known:
        return [], 0.0
    first, last = known[0], known[-1]
    for i in range(first - 1, -1, -1):
        nxt = times[i + 1][0]
        times[i] = (max(0.0, nxt - 0.35), max(0.0, nxt - 0.03))
    for i in range(last + 1, len(tokens)):
        prev = times[i - 1][1]
        times[i] = (prev + 0.03, prev + 0.35)
    for lo, hi in zip(known, known[1:]):
        gap = hi - lo - 1
        if gap <= 0:
            continue
        t0, t1 = times[lo][1], times[hi][0]
        span = max(0.0, t1 - t0)
        # do not smear a few missed words over a long instrumental break
        span = min(span, 0.6 * gap)
        # keep the missed words next to whichever neighbour shares their line
        same_as_prev = tokens[lo + 1][0] == tokens[lo][0]
        base = t0 if same_as_prev else max(t0, t1 - span)
        step = span / gap if gap else 0.0
        for j in range(gap):
            s = base + j * step
            times[lo + 1 + j] = (s, max(s + 0.02, s + step * 0.95))

    lines = []
    for li, text in enumerate(texts):
        ws = [{"start": times[i][0], "end": times[i][1], "text": tok}
              for i, (l, tok) in enumerate(tokens) if l == li]
        if ws:
            lines.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": text, "words": ws})
    # keep line order monotonic
    for prev, cur in zip(lines, lines[1:]):
        if cur["start"] < prev["start"]:
            shift = prev["end"] + 0.05 - cur["start"]
            cur["start"] += shift
            cur["end"] += shift
            for w in cur["words"]:
                w["start"] += shift
                w["end"] += shift
    return lines, ratio


# ------------------------------------------------------- refine line-timed lyrics


def refine_lines(vocals_path: str, lines: list[dict], language: Optional[str] = None,
                 progress: Optional[ProgressFn] = None) -> tuple[list[dict], int]:
    """Lines have rough start/end (from LRC). Find exact word times inside each.

    Returns (lines, number_of_lines_refined).
    """
    import whisperx

    if not lines:
        return lines, 0
    language = language or detect_language(" ".join(l["text"] for l in lines))
    audio = whisperx.load_audio(vocals_path)
    total = len(audio) / 16000.0
    align_model, meta = whisperx.load_align_model(language_code=language, device=DEVICE)

    refined = 0
    for i, line in enumerate(lines):
        nxt = lines[i + 1]["start"] if i + 1 < len(lines) else total
        win_start = max(0.0, line["start"] - 0.4)
        win_end = min(total, max(line["end"], min(nxt, line["start"] + 15.0)) + 0.4)
        tokens = line["text"].split()
        if not tokens or win_end - win_start < 0.3:
            continue
        try:
            res = whisperx.align(
                [{"start": win_start, "end": win_end, "text": line["text"]}],
                align_model, meta, audio, DEVICE, return_char_alignments=False,
            )
            got = [w for seg in res["segments"] for w in seg.get("words", [])]
            timed = [w for w in got if "start" in w and "end" in w]
            if len(got) != len(tokens) or len(timed) < max(1, len(got) // 2):
                continue
            words = []
            for tok, w in zip(tokens, got):
                words.append({"start": w.get("start"), "end": w.get("end"), "text": tok})
            _fill_missing(words, win_start, win_end)
            if abs(words[0]["start"] - line["start"]) > 2.5:
                continue  # alignment wandered off: keep the LRC timing
            line["words"] = words
            line["start"] = words[0]["start"]
            line["end"] = words[-1]["end"]
            refined += 1
        except Exception:
            continue
        finally:
            if progress:
                progress((i + 1) / len(lines))
    _free(align_model)
    return lines, refined


def _fill_missing(words: list[dict], lo: float, hi: float) -> None:
    n = len(words)
    for i, w in enumerate(words):
        if w["start"] is None or w["end"] is None:
            prev = next((words[j]["end"] for j in range(i - 1, -1, -1)
                         if words[j]["end"] is not None), lo)
            nxt = next((words[j]["start"] for j in range(i + 1, n)
                        if words[j]["start"] is not None), min(hi, prev + 0.4))
            w["start"] = float(prev)
            w["end"] = float(max(prev + 0.02, min(nxt, prev + 0.5)))
        else:
            w["start"], w["end"] = float(w["start"]), float(w["end"])
