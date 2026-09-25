"""
Microservice yt-dlp-pot — extração de áudio do YouTube com PO Token.

Resolve o bloqueio do YouTube (PO Token / "Sign in to confirm you're not a bot")
que impede IPs de datacenter de baixar áudio. Usa yt-dlp + bgutil-ytdlp-pot-provider
para gerar PO Tokens válidos automaticamente.

Padrão assíncrono (igual ao BTC): POST /download inicia o job e retorna job_id
na hora (<1s). GET /status/{job_id} consulta o estado. GET /result/{job_id}
devolve o binário do áudio quando pronto. Isso evita o timeout do proxy do
Railway (~100s) em vídeos longos.

Deploy: Railway. Ver README.md.

POST /download  { youtube_url }     ->  { job_id }            (X-API-Key)
GET  /status/{job_id}                ->  { status, error? }   (X-API-Key)
GET  /result/{job_id}                ->  audio/mpeg binary     (X-API-Key)
GET  /health                         ->  { status, bgutil }
"""
import os
import uuid
import tempfile
import subprocess
import threading
import glob
import socket
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

app = FastAPI(title="yt-dlp-pot Service")
API_KEY = os.environ.get("YTDLP_API_KEY", "")
BGUTIL_BASE = os.environ.get("BGUTIL_BASE", "http://127.0.0.1:4416")
BGUTIL_PORT = int(BGUTIL_BASE.rsplit(":", 1)[-1] or "4416")

JOBS_DIR = os.path.join(tempfile.gettempdir(), "ytdlp_jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

_jobs = {}
_jobs_lock = threading.Lock()


class DownloadRequest(BaseModel):
    youtube_url: str


def verify_key(x_api_key: str):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def run_ytdlp(job_id: str, youtube_url: str):
    output_template = os.path.join(JOBS_DIR, f"{job_id}.%(ext)s")
    cmd = [
        "python3", "-m", "yt_dlp",
        "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "--no-playlist", "--quiet", "--no-progress", "--no-warnings",
        "--extractor-args", f"youtubepot-bgutilhttp:base_url={BGUTIL_BASE}",
        "-o", output_template,
        youtube_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = (result.stderr or "yt-dlp falhou (código %d)" % result.returncode)[-2000:]
            return

        audio_path = os.path.join(JOBS_DIR, f"{job_id}.mp3")
        if not os.path.exists(audio_path):
            candidates = [f for f in glob.glob(os.path.join(JOBS_DIR, f"{job_id}.*")) if not f.endswith(".info.json")]
            if candidates:
                audio_path = candidates[0]

        if not os.path.exists(audio_path):
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = "Áudio não encontrado após download"
            return

        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["audio_path"] = audio_path
    except subprocess.TimeoutExpired:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "yt-dlp timeout (300s)"
        for f in glob.glob(os.path.join(JOBS_DIR, f"{job_id}.*")):
            try:
                os.unlink(f)
            except Exception:
                pass
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)


@app.post("/download")
async def download(req: DownloadRequest, x_api_key: str = Header(...)):
    verify_key(x_api_key)
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "processing", "audio_path": None, "error": None}
    thread = threading.Thread(target=run_ytdlp, args=(job_id, req.youtube_url), daemon=True)
    thread.start()
    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
async def status(job_id: str, x_api_key: str = Header(...)):
    verify_key(x_api_key)
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail="Job não encontrado")
        job = _jobs[job_id]
        return JSONResponse({"status": job["status"], "error": job["error"]})


@app.get("/result/{job_id}")
async def result(job_id: str, x_api_key: str = Header(...)):
    verify_key(x_api_key)
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(status_code=404, detail="Job não encontrado")
        job = _jobs[job_id]
    if job["status"] != "done" or not job["audio_path"] or not os.path.exists(job["audio_path"]):
        raise HTTPException(status_code=409, detail="Áudio não pronto")
    return FileResponse(job["audio_path"], media_type="audio/mpeg", filename=f"{job_id}.mp3")


@app.get("/health")
async def health():
    bgutil_ok = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", BGUTIL_PORT))
        s.close()
        bgutil_ok = True
    except Exception:
        bgutil_ok = False
    return {"status": "ok", "bgutil": bgutil_ok}
