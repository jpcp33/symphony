from fastapi import FastAPI, HTTPException, Header, Response
from fastapi.responses import StreamingResponse
import yt_dlp
import io
import os
import tempfile
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")

def check_key(x_api_key: str = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/extract")
def extract(body: dict, _: str = Header(None, alias="X-API-Key")):
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url é obrigatório")

    tmp = tempfile.mkdtemp()
    outtmpl = os.path.join(tmp, "audio.%(ext)s")

    # Usa o cliente android que dribla a parede "Please sign in" do YouTube
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "web"],
            }
        },
        # Tenta contornar proteção antrobô
        "geo_bypass": True,
        "geo_bypass_country": "BR",
        "socket_timeout": 60,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            ext = info.get("ext", "mp3")
            title = info.get("title", "audio")
            logger.info(f"Baixado: {filename}")
    except Exception as e:
        logger.error(f"yt-dlp error: {e}")
        raise HTTPException(status_code=502, detail=f"yt-dlp error: {e}")

    if not os.path.exists(filename):
        raise HTTPException(status_code=500, detail="Arquivo não gerado")

    with open(filename, "rb") as f:
        data = f.read()

    os.remove(filename)

    return Response(
        content=data,
        media_type="audio/mpeg",
        headers={"Content-Disposition": f"attachment; filename=audio.{ext}"}
    )
