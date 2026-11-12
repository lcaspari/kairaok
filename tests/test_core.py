"""Checks for the parts that need no models: python tests/test_core.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kairaok import align, download, lyrics

LRC = """[ti:Test]
[00:12.00]Hello from the other side
[00:16.50]I must have called a thousand times
[00:21.00]
[00:30.25]To tell you I'm sorry
"""

def test_lrc():
    lines = lyrics.lines_from_lrc(LRC, 60)
    assert [l["start"] for l in lines] == [12.0, 16.5, 30.25]
    assert lines[1]["end"] == 21.0          # the empty stamp ends the line
    assert len(lines[0]["words"]) == 5
    assert lyrics.is_lrc(LRC) and not lyrics.is_lrc("just\nplain\ntext")

def test_enhanced_lrc():
    lines = lyrics.lines_from_lrc("[00:05.00]<00:05.00>Hey <00:05.80>you <00:06.40>there\n[00:09.00]<00:09.00>x\n[00:12.00]<00:12.00>y", 20)
    assert [w["text"] for w in lines[0]["words"]] == ["Hey", "you", "there"]
    assert lines[0]["words"][1]["start"] == 5.8 and lines[0].get("word_timed")

def test_trim():
    lines = lyrics.lines_from_lrc(LRC, 60)
    notes = [{"start": 12.1, "end": 13.0, "midi": 60}, {"start": 13.1, "end": 14.9, "midi": 62},
             {"start": 16.6, "end": 20.0, "midi": 60}, {"start": 30.3, "end": 32.0, "midi": 64}]
    lyrics.trim_line_ends(lines, notes)
    assert abs(lines[0]["end"] - 15.0) < 0.01, lines[0]["end"]
    assert lines[0]["words"][-1]["end"] <= 15.01

def test_map():
    texts = ["Hello from the other side", "I must have called a thousand times"]
    heard = [dict(text=t, start=s, end=s + 0.3) for t, s in
             [("hello", 12.0), ("from", 12.5), ("the", 12.9), ("side", 14.0),      # missed "other"
              ("I", 16.5), ("must've", 16.8), ("called", 17.5), ("a", 18.0), ("thousand", 18.2), ("times", 19.0)]]
    lines, ratio = align.map_lyrics(texts, heard)
    assert ratio > 0.7
    assert lines[0]["start"] == 12.0 and lines[1]["start"] == 16.5
    other = lines[0]["words"][3]
    assert 13.2 <= other["start"] <= 14.0, other
    flat = [w for l in lines for w in l["words"]]
    assert all(b["start"] >= a["start"] for a, b in zip(flat, flat[1:]))

def test_titles():
    g = download.guess_artist_title
    assert g({"title": "Adele - Hello (Official Music Video)"}) == ("Adele", "Hello")
    assert g({"title": "Queen – Bohemian Rhapsody [Remastered 2011] | HD"}) == ("Queen", "Bohemian Rhapsody")
    assert g({"artist": "Nena, Kim Wilde", "track": "99 Luftballons", "title": "x"}) == ("Nena", "99 Luftballons")
    assert g({"title": "Hello (Lyrics)", "channel": "Adele - Topic"}) == ("Adele", "Hello")

def test_finalize():
    out = lyrics.finalize([{"start": 5, "end": 4, "text": "a b", "words": []},
                           {"start": 1, "end": 2, "text": " ", "words": []}], 10)
    assert len(out) == 1 and out[0]["end"] > out[0]["start"] and len(out[0]["words"]) == 2

def test_text_match():
    r = align.text_match(["Hello from the other side", "I must have called"], "hello from other side i must called".split())
    assert r["expected"] == 9 and abs(r["score"] - 7 / 9) < 0.01, r

def test_reconcile():
    from kairaok import sync
    notes = [{"start": 10.0, "end": 12.0, "midi": 60}, {"start": 12.1, "end": 16.0, "midi": 62},
             {"start": 20.0, "end": 21.0, "midi": 64}]
    lines = [{"start": 10.05, "end": 13.0, "text": "one two three", "words": [
        {"start": 10.05, "end": 11.0, "text": "one"}, {"start": 11.0, "end": 12.05, "text": "two"},
        {"start": 12.05, "end": 13.0, "text": "three"}]}]
    n2, l2 = sync.reconcile(notes, lines, True)
    assert l2[0]["words"][0]["start"] == 10.0 and l2[0]["words"][2]["start"] == 12.1   # snapped to notes
    assert [round(n["start"], 2) for n in n2] == [10.0, 11.0, 12.1], n2                  # split at "two", ad-lib at 20 s dropped
    assert n2[-1]["end"] <= 13.0 + 0.36                                                   # tail trimmed
    assert notes[1]["end"] == 16.0 and lines[0]["words"][0]["start"] == 10.05            # inputs untouched

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
