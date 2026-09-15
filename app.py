"""
Microservice BTC (ChordMini) — detecção de acordes via modelo BTC-SL.

Recebe um audio_url, baixa o áudio, divide em chunks de 60s com ffmpeg,
roda o modelo BTC do ChordMini (commit 3d186fc) em cada chunk e mescla
as timelines de acordes. O chunking evita timeout do proxy do Railway
(~100s) — cada chunk processa em ~20-30s no CPU free tier.

Deploy: Railway. Ver README.md em base44/shared/btc-microservice/.

POST /analyze  { audio_url }  ->  { chords: [{ chord, start, end }, ...] }
GET  /health                  ->  { status: "ok" }
"""
import os
import sys
import json
import tempfile
import subprocess
import urllib.request
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="BTC Chord Service")
API_KEY = os.environ.get("BTC_API_KEY", "")

# Diretório do ChordMini clonado no build do Docker (commit 3d186fc)
CHORDMINI_DIR = os.environ.get("CHORDMINI_DIR", "/app/ChordMini")

# Duração de cada chunk em segundos. 60s mantém cada inferência < 100s no CPU free.
CHUNK_SECONDS = 60.0

# Importa o wrapper UMA VEZ no startup (evita reimport por chunk)
_btc_module = None


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
    """Lê um .lab (start\\tend\\tchord) e retorna [{chord, start, end}, ...]."""
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
    """Usa ffprobe para obter a duração do áudio em segundos."""
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
    """Divide o áudio em chunks de chunk_sec segundos. Retorna lista de (path, offset)."""
    duration = get_audio_duration(audio_path)
    if duration == 0:
        # Fallback: não consegue medir, processa inteiro
        return [(audio_path, 0.0)]

    chunks = []
    offset = 0.0
    idx = 0
    while offset < duration:
        chunk_path = tempfile.NamedTemporaryFile(suffix=f"_{idx}.wav", delete=False).name
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(offset), "-t", str(chunk_sec),
                "-i", audio_path, "-ac", "1", "-ar", "22050", chunk_path,
            ],
            capture_output=True, timeout=30,
        )
        if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 500:
            chunks.append((chunk_path, offset))
        offset += chunk_sec
        idx += 1
    return chunks


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, x_api_key: str = Header(...)):
    verify_key(x_api_key)

    # 1. Baixa o áudio completo para um arquivo temporário
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        urllib.request.urlretrieve(req.audio_url, tmp_path)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=502, detail=f"Download falhou: {e}")

    try:
        btc_fn = _load_btc()

        # 2. Divide em chunks de 60s
        chunks = split_audio(tmp_path, CHUNK_SECONDS)
        if not chunks:
            raise HTTPException(status_code=500, detail="Falha ao dividir áudio em chunks.")

        all_chords = []
        for chunk_path, offset in chunks:
            lab_path = tempfile.NamedTemporaryFile(suffix=".lab", delete=False).name
            try:
                ok = btc_fn(chunk_path, lab_path, model_variant="sl")
                if ok:
                    chunk_chords = parse_lab_file(lab_path)
                    # Aplica offset temporal para reconstruir a timeline completa
                    for c in chunk_chords:
                        all_chords.append({
                            "chord": c["chord"],
                            "start": c["start"] + offset,
                            "end": c["end"] + offset,
                        })
            except Exception:
                # Continua processando outros chunks mesmo se um falhar
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

        if not all_chords:
            raise HTTPException(
                status_code=500,
                detail="Nenhum acorde detectado em nenhum chunk.",
            )

        return JSONResponse({"chords": all_chords})
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"btc_chord_recognition não encontrado em {CHORDMINI_DIR}. Verifique o Dockerfile. Erro: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inferência falhou: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@app.get("/health")
async def health():
    return {"status": "ok"}
