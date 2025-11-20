FROM python:3.11-slim

WORKDIR /app

# Install FFmpeg saja
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Install dependencies
RUN pip install --no-cache-dir fastapi uvicorn aiofiles ffmpeg-python

# Install Whisper TANPA model besar
RUN pip install --no-cache-dir openai-whisper

# Copy hanya file yang diperlukan
COPY auto_subtitle_injector_full.py .

CMD python -m uvicorn auto_subtitle_injector_full:app --host 0.0.0.0 --port $PORT
