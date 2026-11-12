"""Build a synthetic song (click + bass backing, sung-like melody) for testing
without any downloads. Writes vocals.wav, instrumental.wav, mix.wav and truth.json."""
import json, sys
from pathlib import Path
import numpy as np, soundfile as sf

def make(out: Path, bpm=112.0, bars=24, sr=44100, seed=3):
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    dur = bars * 4 * beat + 2.0
    n = int(dur * sr)
    t = np.arange(n) / sr
    inst = np.zeros(n)
    for b in range(bars * 4):
        s = int((1.0 + b * beat) * sr)
        L = int(0.12 * sr); tt = np.arange(L) / sr
        f = 55 if b % 4 == 0 else 110
        kick = np.sin(2*np.pi*(f + 80*np.exp(-tt*40))*tt) * np.exp(-tt*25) * (1.0 if b % 4 == 0 else 0.5)
        inst[s:s+L] += kick
        if b % 2 == 1:
            inst[s:s+L] += rng.normal(0, 0.25, L) * np.exp(-tt*40)
    scale = [57, 59, 60, 62, 64, 65, 67, 69, 71, 72]
    notes, voc = [], np.zeros(n)
    pos = 1.0 + 8 * beat           # two bars intro
    while pos < dur - 3:
        if rng.random() < 0.18:
            pos += beat * rng.choice([1, 2]); continue
        d = beat * rng.choice([0.5, 1, 1, 1.5, 2])
        m = int(rng.choice(scale))
        s, e = int(pos * sr), int((pos + d * 0.92) * sr)
        tt = np.arange(e - s) / sr
        f0 = 440 * 2 ** ((m - 69) / 12) * (1 + 0.006 * np.sin(2*np.pi*5.5*tt))
        ph = 2 * np.pi * np.cumsum(f0) / sr
        w = sum(np.sin(k * ph) / k for k in range(1, 7))
        env = np.minimum(1, tt / 0.03) * np.minimum(1, (tt[-1] - tt) / 0.04)
        voc[s:e] += 0.25 * w * env
        notes.append({"start": round(pos, 3), "end": round(pos + d * 0.92, 3), "midi": m})
        pos += d
    inst *= 0.6
    out.mkdir(parents=True, exist_ok=True)
    st = lambda x: np.stack([x, x], axis=1).astype("float32")
    sf.write(out / "vocals.wav", st(voc), sr)
    sf.write(out / "instrumental.wav", st(inst), sr)
    sf.write(out / "mix.wav", st(np.clip(voc + inst, -1, 1)), sr)
    json.dump({"bpm": bpm, "notes": notes, "duration": dur}, open(out / "truth.json", "w"))
    return notes

if __name__ == "__main__":
    make(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/_song"))
