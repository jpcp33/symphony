"""
Microservice BTC (ChordMini) — detecção de acordes via modelo BTC-SL.

Padrão assíncrono: POST /analyze inicia o processamento em background
e retorna job_id imediatamente (<1s). GET /status/{job_id} consulta o
resultado. Isso evita o timeout do proxy do Railway (~100s).

O áudio é dividido em chunks de 60s com ffmpeg; cada chunk é processado
sequencialmente pelo modelo BTC do ChordMini (commit 3d186fc). O estado
do job fica em memória (dicionário global).

Deploy: Railway. Ver README.md em base44/shared/btc-microservice/.

POST /analyze  { audio_url }            ->  { job_id }
GET  /status/{job_id}                    ->  { status, chords?, error? }
GET  /health                             ->  { status: "ok" }
"""
import os
import sys
import json
import uuid
import tempfile
import subprocess
import urllib.request
import threading
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="BTC Chord Service")
API_KEY = os.environ.get("BTC_API_KEY", "")

CHORDMINI_DIR = os.environ.get("CHORDMINI_DIR", "/app/ChordMini")
CHUNK_SECONDS = 60.0

# Estado dos jobs em memória: {job_id: {status, chords, error, total_chunks, done_chunks}}
_jobs = {}
_btc_module = None
_btc_lock = threading.Lock()


def _load_btc():
    global _btc_module
    if _btc_module is None:
        sys.path.insert(0, CHORDMINI_DIR)
        from btc_chord_recognition import btc_chord_recognition
        _btc_module = btc_chord_recognition
    return _btc_module


class AnalyzeRequest(BaseModel):
    audio_url: str


def verify_key(x_api_key: str):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def parse_lab_file(lab_path):
    chords = []
    with open(lab_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                chords.append({
                    "chord": parts[2],
                    "start": float(parts[0]),
                    "end": float(parts[1]),
                })
    return chords


def get_audio_duration(audio_path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception:
        return 0.0


def split_audio(audio_path, chunk_sec):
    duration = get_audio_duration(audio_path)
    if duration == 0:
        return [(audio_path, 0.0)]
    chunks = []
    offset = 0.0
    idx = 0
    while offset < duration:
        chunk_path = tempfile.NamedTemporaryFile(suffix=f"_{idx}.wav", delete=False).name
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(offset), "-t", str(chunk_sec),
             "-i", audio_path, "-ac", "1", "-ar", "22050", chunk_path],
            capture_output=True, timeout=30,
        )
        if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 500:
            chunks.append((chunk_path, offset))
        offset += chunk_sec
        idx += 1
    return chunks


def process_job(audio_url, job_id):
    """Roda em background: baixa áudio, divide em chunks, processa cada um."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        urllib.request.urlretrieve(audio_url, tmp_path)

        with _btc_lock:
            btc_fn = _load_btc()

        chunks = split_audio(tmp_path, CHUNK_SECONDS)
        _jobs[job_id]["total_chunks"] = len(chunks)

        all_chords = []
        for chunk_path, offset in chunks:
            lab_path = tempfile.NamedTemporaryFile(suffix=".lab", delete=False).name
            try:
                with _btc_lock:
                    ok = btc_fn(chunk_path, lab_path, model_variant="sl")
                if ok:
                    chunk_chords = parse_lab_file(lab_path)
                    for c in chunk_chords:
                        all_chords.append({
                            "chord": c["chord"],
                            "start": c["start"] + offset,
                            "end": c["end"] + offset,
                        })
            except Exception:
                pass
            finally:
                if os.path.exists(lab_path):
                    try:
                        os.unlink(lab_path)
                    except Exception:
                        pass
                if chunk_path != tmp_path and os.path.exists(chunk_path):
                    try:
                        os.unlink(chunk_path)
                    except Exception:
                        pass
                _jobs[job_id]["done_chunks"] += 1

        if not all_chords:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "Nenhum acorde detectado."
        else:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["chords"] = all_chords
    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, x_api_key: str = Header(...)):
    verify_key(x_api_key)

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "processing",
        "chords": None,
        "error": None,
        "total_chunks": 0,
        "done_chunks": 0,
    }

    thread = threading.Thread(target=process_job, args=(req.audio_url, job_id), daemon=True)
    thread.start()

    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
async def status(job_id: str, x_api_key: str = Header(...)):
    verify_key(x_api_key)
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    job = _jobs[job_id]
    return JSONResponse({
        "status": job["status"],
        "chords": job["chords"],
        "error": job["error"],
        "total_chunks": job["total_chunks"],
        "done_chunks": job["done_chunks"],
    })


@app.get("/health")
async def health():
    return {"status": "ok"}
