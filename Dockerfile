FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv ffmpeg git curl ca-certificates \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libjpeg62-turbo \
    libgif7 librsvg2-2 libpixman-1-0 libxcb1 libx11-6 libxext6 libxrender1 && \
    rm -rf /var/lib/apt/lists/*

RUN git clone --single-branch --branch 2.0.0 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /bgutil && \
    cd /bgutil/server && npm ci && npx tsc

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

WORKDIR /app
COPY app.py /app/app.py
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENV BGUTIL_BASE=http://127.0.0.1:4416
EXPOSE 8000

CMD ["/app/start.sh"]
