FROM python:3.11-slim

WORKDIR /app

# Install minimal dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages secara individual
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir fastapi uvicorn aiofiles
RUN pip install --no-cache-dir ffmpeg-python requests
RUN pip install --no-cache-dir googletrans==4.0.0-rc1 m3u8

COPY main.py .

CMD python -m uvicorn main:app --host 0.0.0.0 --port $PORT
