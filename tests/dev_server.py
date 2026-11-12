"""Run the app without Demucs, YouTube or LRCLIB, for testing the rest:
stems come from tests/_song, lyrics from a made-up synced file.

    python tests/dev_server.py [port]
"""
import json, shutil, sys, os
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("KAIRAOK_LIBRARY", str(HERE / "_library"))

import soundfile as sf
from kairaok import config, lyrics, separate, server
import make_test_song

SONG = HERE / "_song"
if not (SONG / "truth.json").exists():
    make_test_song.make(SONG)
truth = json.load(open(SONG / "truth.json"))

def fake_separate(wav_path, out_dir):
    for src, dst in (("vocals.wav", "vocals.flac"), ("instrumental.wav", "instrumental.flac")):
        data, sr = sf.read(str(SONG / src), dtype="float32", always_2d=True)
        sf.write(str(out_dir / dst), data, sr, subtype="PCM_16")
    return out_dir / "vocals.flac", out_dir / "instrumental.flac", "fake"

def fake_find(artist, title, duration):
    words = "we are testing the karaoke machine tonight so sing along with every single note you hear".split()
    out, k = [], 0
    notes = truth["notes"]
    for i in range(0, len(notes), 5):
        chunk = notes[i:i + 5]
        text = " ".join(words[(k + j) % len(words)] for j in range(len(chunk)))
        k += len(chunk)
        t = chunk[0]["start"]
        out.append(f"[{int(t // 60):02d}:{t % 60:05.2f}]{text.capitalize()}")
    return lyrics.LyricsHit(synced="\n".join(out), plain=None, trusted=True, duration_diff=0.0,
                            track=title, artist=artist)

separate.separate = fake_separate
lyrics.find_lyrics = fake_find

if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8643
    uvicorn.run(server.app, host="127.0.0.1", port=port, log_level="warning")
