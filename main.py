from fastapi import FastAPI, HTTPException, Header, Response
import yt_dlp
import os
import tempfile
import logging
import base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")
COOKIES_B64 = os.environ.get("COOKIES_B64", "")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/extract")
def extract(body: dict, x_api_key: str = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url é obrigatório")

    tmp = tempfile.mkdtemp()
    outtmpl = os.path.join(tmp, "audio.%(ext)s")
    cookiefile = None

    if COOKIES_B64:
        cookiefile = os.path.join(tmp, "cookies.txt")
        with open(cookiefile, "wb") as f:
            f.write(base64.b64decode(COOKIES_B64))

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": False,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "geo_bypass_country": "BR",
        "socket_timeout": 60,
        "extractor_args": {"youtube": {"player_client": ["web", "android"]}},
    }
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            ext = info.get("ext", "mp3")
            logger.info(f"Baixado: {filename}")
    except Exception as e:
        logger.error(f"yt-dlp error: {e}")
        raise HTTPException(status_code=502, detail=f"yt-dlp error: {e}")

    if not os.path.exists(filename):
        raise HTTPException(status_code=500, detail="Arquivo não gerado")

    with open(filename, "rb") as f:
        data = f.read()
    os.remove(filename)
    if cookiefile and os.path.exists(cookiefile):
        os.remove(cookiefile)

    return Response(content=data, media_type="audio/mpeg",
        headers={"Content-Disposition": f"attachment; filename=audio.{ext}"})
