import os
import tempfile
import yt_dlp
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI()
API_KEY = os.environ.get("API_KEY", "")

@app.post("/extract")
async def extract_audio(payload: dict, x_api_key: str = Header(None, alias="X-API-Key")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")

    url = payload.get("url")
    if not url:
        raise HTTPException(400, "url is required")

    tmp_dir = tempfile.mkdtemp()
    base_path = os.path.join(tmp_dir, "audio")

    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "outtmpl": base_path,
        "noplaylist": True,
        "quiet": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        raise HTTPException(500, f"yt-dlp error: {str(e)}")

    final_path = base_path + ".mp3"
    if not os.path.exists(final_path):
        raise HTTPException(500, "Audio extraction failed")

    def cleanup():
        try:
            os.remove(final_path)
            os.rmdir(tmp_dir)
        except OSError:
            pass

    return FileResponse(
        final_path,
        media_type="audio/mpeg",
        filename="audio.mp3",
        background=BackgroundTask(cleanup),
    )

@app.get("/health")
async def health():
    return {"status": "ok"}
