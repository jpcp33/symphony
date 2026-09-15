FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg git curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN git clone https://github.com/ptnghia-j/ChordMini.git /app/ChordMini && \
    cd /app/ChordMini && \
    git checkout 3d186fc9c9c97e342bb444490996b5798a7c348f && \
    rm -rf .git

RUN mkdir -p /app/ChordMini/checkpoints/SL && \
    curl -fsSL -o /app/ChordMini/checkpoints/SL/btc_model_large_voca.pt \
    https://raw.githubusercontent.com/ptnghia-j/ChordMini/main/checkpoints/btc_model_large_voca.pt

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV CHORDMINI_DIR="/app/ChordMini"
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
