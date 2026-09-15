"""
Microservice BTC (ChordMini) — protótipo de detecção de acordes.

Recebe um audio_url, baixa o áudio, roda o modelo BTC do ChordMini
(github.com/ptnghia-j/ChordMini) e retorna a timeline de acordes
com timestamps reais — sem snap para downbeat, com smoothing nativo
do transformador.

Deploy: Railway (mesmo padrão do yt-dlp). Ver README.md em base44/shared/btc-microservice/.

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


class AnalyzeRequest(BaseModel):
    audio_url: str


def verify_key(x_api_key: str):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, x_api_key: str = Header(...)):
    verify_key(x_api_key)

    # 1. Baixa o audio para um arquivo temporario
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        urllib.request.urlretrieve(req.audio_url, tmp_path)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Download falhou: {e}")

    try:
        # 2. Inferencia usando o pipeline do ChordMini (BTC model).
        #    O repo e clonado em /app/ChordMini no build do Docker.
        #    Ajuste o import e a chamada conforme a API real do ChordMini -
        #    a estrutura do repo tem python_backend/models/ChordMini/.
        sys.path.insert(0, "/app/ChordMini/python_backend")

        # TODO: ajustar ao API real do ChordMini apos clonar o repo.
        # O pipeline tipico: carregar o modelo BTC -> extrair features
        # (HCQT/chroma) -> inferencia -> pos-processamento (smoothing).
        from models.ChordMini.inference import predict_chords  # ajustar nome real

        chords = predict_chords(tmp_path)
        # chords esperado: [{ "chord": "Am", "start": 0.0, "end": 2.5 }, ...]

        return JSONResponse({"chords": chords})
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"ChordMini nao encontrado em /app/ChordMini. Verifique o Dockerfile. Erro: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inference falhou: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.get("/health")
async def health():
    return {"status": "ok"}
