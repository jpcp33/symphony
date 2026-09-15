"""
Microservice BTC (ChordMini) — detecção de acordes via modelo BTC-SL.

Recebe um audio_url, baixa o áudio, roda o modelo BTC do ChordMini
(commit 3d186fc — github.com/ptnghia-j/ChordMini) usando o wrapper real
btc_chord_recognition.py e retorna a timeline de acordes com timestamps
reais (formato .lab parseado), sem snap para downbeat — o smoothing é
nativo do transformador.

Deploy: Railway. Ver README.md em base44/shared/btc-microservice/.

POST /analyze  { audio_url }  ->  { chords: [{ chord, start, end }, ...] }
GET  /health                  ->  { status: "ok" }
"""
import os
import sys
import tempfile
import urllib.request
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="BTC Chord Service")
API_KEY = os.environ.get("BTC_API_KEY", "")

# Diretório do ChordMini clonado no build do Docker (commit 3d186fc)
CHORDMINI_DIR = os.environ.get("CHORDMINI_DIR", "/app/ChordMini")


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


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, x_api_key: str = Header(...)):
    verify_key(x_api_key)

    # 1. Baixa o áudio para um arquivo temporário
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        urllib.request.urlretrieve(req.audio_url, tmp_path)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=502, detail=f"Download falhou: {e}")

    # 2. Arquivo .lab de saída
    with tempfile.NamedTemporaryFile(suffix=".lab", delete=False) as lab_tmp:
        lab_path = lab_tmp.name

    try:
        # 3. Inferência usando o wrapper real do ChordMini (btc_chord_recognition.py)
        sys.path.insert(0, CHORDMINI_DIR)
        from btc_chord_recognition import btc_chord_recognition

        ok = btc_chord_recognition(tmp_path, lab_path, model_variant="sl")
        if not ok:
            raise HTTPException(
                status_code=500,
                detail="btc_chord_recognition retornou False (checkpoint ausente ou erro de inferência).",
            )

        chords = parse_lab_file(lab_path)
        return JSONResponse({"chords": chords})
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
        for p in (tmp_path, lab_path):
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass


@app.get("/health")
async def health():
    return {"status": "ok"}
