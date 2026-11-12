"""Stem separation with Demucs: split a song into vocals and instrumental."""

from __future__ import annotations

from pathlib import Path

from . import config


def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"  # Apple Silicon GPU
    return "cpu"


def separate(wav_path: Path, out_dir: Path) -> tuple[Path, Path, str]:
    """Write vocals.flac and instrumental.flac. Returns (vocals, instrumental, device)."""
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    if audio.shape[1] == 1:
        audio = audio.repeat(2, axis=1)
    model = get_model(config.DEMUCS_MODEL)
    model.eval()
    if sr != model.samplerate:
        raise RuntimeError(f"Expected {model.samplerate} Hz audio, got {sr} Hz.")

    wav = torch.from_numpy(audio.T.copy())           # (channels, samples)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    mix = ((wav - mean) / std)[None]                 # (1, channels, samples)

    device = pick_device()

    def run(dev: str):
        with torch.no_grad():
            return apply_model(model, mix, device=dev, shifts=0, split=True,
                               overlap=0.25, progress=False)[0]

    try:
        sources = run(device)
    except Exception:
        if device == "cpu":
            raise
        device = "cpu"                               # GPU hiccup: retry on CPU
        sources = run(device)

    sources = sources.cpu() * std + mean             # (stems, channels, samples)
    names = list(model.sources)
    vocals = sources[names.index("vocals")]
    instrumental = wav - vocals

    vocals_path = out_dir / "vocals.flac"
    instr_path = out_dir / "instrumental.flac"
    sf.write(str(vocals_path), vocals.T.numpy().clip(-1, 1), sr, subtype="PCM_16")
    sf.write(str(instr_path), instrumental.T.numpy().clip(-1, 1), sr, subtype="PCM_16")
    return vocals_path, instr_path, device
