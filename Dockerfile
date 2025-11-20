FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements sederhana
COPY requirements.txt .

# Install dependencies satu-satu
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir fastapi uvicorn aiofiles
RUN pip install --no-cache-dir openai-whisper
RUN pip install --no-cache-dir ffmpeg-python

# Copy app
COPY . .

CMD python -m uvicorn auto_subtitle_injector_full:app --host 0.0.0.0 --port $PORT
