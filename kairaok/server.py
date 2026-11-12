"""Local web server: the song library API and the sing-along page."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import align, config, pipeline

app = FastAPI(title="Kairaok")
worker = ThreadPoolExecutor(max_workers=1)   # one song at a time: the models are heavy

_ID = re.compile(r"^[0-9a-f]{10}$")
_MEDIA = {"vocals.flac", "instrumental.flac", "original.wav"}


def _meta_or_404(song_id: str) -> dict:
    if not _ID.match(song_id):
        raise HTTPException(404, "Song not found")
    meta = pipeline.read_meta(song_id)
    if meta is None:
        raise HTTPException(404, "Song not found")
    return meta


@app.on_event("startup")
def _startup() -> None:
    config.LIBRARY.mkdir(parents=True, exist_ok=True)
    pipeline.mark_interrupted()


@app.get("/api/status")
def status() -> dict:
    return {"whisperx": align.available(), "whisper_model": config.WHISPER_MODEL,
            "demucs_model": config.DEMUCS_MODEL}


@app.get("/api/songs")
def songs() -> list[dict]:
    return [{k: v for k, v in m.items() if k != "pasted_lyrics"} for m in pipeline.list_songs()]


@app.post("/api/songs")
async def add_song(
    url: str = Form(""), artist: str = Form(""), title: str = Form(""),
    lyrics: str = Form(""), file: Optional[UploadFile] = File(None),
) -> dict:
    url = url.strip()
    has_file = file is not None and file.filename
    if not url and not has_file:
        raise HTTPException(400, "Paste a YouTube link or choose an audio file.")
    if url and not re.match(r"^https?://", url):
        raise HTTPException(400, "That does not look like a link. It should start with https://")

    upload_path = None
    filename = ""
    if has_file and not url:
        filename = Path(file.filename).stem
    meta = pipeline.create_song(url=url, artist=artist, title=title, pasted_lyrics=lyrics,
                                filename=filename)
    if has_file and not url:
        suffix = Path(file.filename).suffix[:8] or ".bin"
        upload_path = pipeline.song_dir(meta["id"]) / f"upload{suffix}"
        with open(upload_path, "wb") as out:
            while chunk := await file.read(1 << 20):
                out.write(chunk)
    worker.submit(pipeline.process_song, meta["id"], upload_path)
    return meta


@app.get("/api/songs/{song_id}")
def song(song_id: str) -> dict:
    meta = _meta_or_404(song_id)
    meta.pop("pasted_lyrics", None)
    return meta


@app.get("/api/songs/{song_id}/data")
def song_data(song_id: str):
    _meta_or_404(song_id)
    p = pipeline.song_dir(song_id) / "song.json"
    if not p.exists():
        raise HTTPException(404, "This song is not ready yet")
    song = json.loads(p.read_text(encoding="utf-8"))
    song.pop("notes_raw", None)                     # only needed for re-syncing
    song.get("lyrics", {}).pop("raw_lines", None)
    return JSONResponse(song)


@app.get("/media/{song_id}/{name}")
def media(song_id: str, name: str):
    _meta_or_404(song_id)
    if name not in _MEDIA:
        raise HTTPException(404, "Not found")
    p = pipeline.song_dir(song_id) / name
    if not p.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(p)


class LyricsRequest(BaseModel):
    artist: str = ""
    title: str = ""
    lyrics: str = ""
    mode: str = "auto"          # auto | ear | transcribe


@app.post("/api/songs/{song_id}/lyrics")
def redo_lyrics(song_id: str, req: LyricsRequest) -> dict:
    meta = _meta_or_404(song_id)
    if meta.get("status") != "ready":
        raise HTTPException(409, "This song is still being prepared")
    if req.mode not in ("auto", "ear", "transcribe"):
        raise HTTPException(400, "Unknown mode")
    meta = pipeline.write_meta(song_id, status="processing", step="lyrics",
                               step_label="Finding and timing the lyrics", progress=0.02)
    worker.submit(pipeline.redo_lyrics, song_id, req.artist.strip() or meta.get("artist", ""),
                  req.title.strip() or meta.get("title", ""), req.lyrics, req.mode)
    return meta


@app.post("/api/songs/{song_id}/melody")
def redo_melody(song_id: str) -> dict:
    meta = _meta_or_404(song_id)
    if meta.get("status") != "ready":
        raise HTTPException(409, "This song is still being prepared")
    meta = pipeline.write_meta(song_id, status="processing", step="melody",
                               step_label="Tracing the melody", progress=0.02)
    worker.submit(pipeline.redo_melody, song_id)
    return meta


@app.post("/api/songs/{song_id}/text-score")
def text_score(song_id: str, audio: UploadFile = File(...), start: float = Form(0.0),
               end: float = Form(1e9)) -> dict:
    """How much of the lyrics was recognisably sung in this microphone recording."""
    _meta_or_404(song_id)
    d = pipeline.song_dir(song_id)
    p = d / "song.json"
    if not p.exists() or not align.available():
        return {"available": False}
    song = json.loads(p.read_text(encoding="utf-8"))
    texts = [l["text"] for l in song["lyrics"]["lines"] if l["end"] > start and l["start"] < end]
    if not texts:
        return {"available": False}
    suffix = Path(audio.filename or "take.webm").suffix[:8] or ".webm"
    take = d / f"take{suffix}"
    try:
        with open(take, "wb") as out:
            while chunk := audio.file.read(1 << 20):
                out.write(chunk)
        # queued behind song preparation so two heavy models never run at once
        result = worker.submit(align.score_recording, str(take), texts,
                               song["lyrics"].get("language")).result()
        return {"available": True, **result}
    except Exception as exc:
        return {"available": False, "error": str(exc)[:200]}
    finally:
        take.unlink(missing_ok=True)


class ScoreRequest(BaseModel):
    score: int
    accuracy: float = 0.0


@app.post("/api/songs/{song_id}/score")
def save_score(song_id: str, req: ScoreRequest) -> dict:
    meta = _meta_or_404(song_id)
    best = meta.get("best_score")
    if best is None or req.score > best:
        meta = pipeline.write_meta(song_id, best_score=int(req.score),
                                   best_accuracy=round(float(req.accuracy), 3))
    return {"best_score": meta.get("best_score"), "is_best": meta.get("best_score") == req.score}


@app.delete("/api/songs/{song_id}")
def remove(song_id: str) -> dict:
    meta = _meta_or_404(song_id)
    if meta.get("status") == "processing":
        raise HTTPException(409, "Wait until the song has finished preparing")
    pipeline.delete_song(song_id)
    return {"ok": True}


@app.middleware("http")
async def _no_cache(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/media/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")


def main() -> None:
    import uvicorn

    print(f"\n  Kairaok is running: http://{config.HOST}:{config.PORT}\n  Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
