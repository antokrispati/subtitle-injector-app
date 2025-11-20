FROM python:3.11-slim

WORKDIR /app

# Install system dependencies termasuk CURL
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    aiofiles \
    ffmpeg-python \
    requests \
    googletrans==4.0.0-rc1 \
    m3u8

COPY . .

CMD python -m uvicorn main:app --host 0.0.0.0 --port $PORT
