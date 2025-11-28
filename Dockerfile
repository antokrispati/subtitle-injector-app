FROM python:3.11-slim

WORKDIR /app

# Install hanya yang essential
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir fastapi uvicorn

COPY main.py .

# Gunakan PORT environment variable dari Railway
CMD uvicorn main:app --host 0.0.0.0 --port $PORT --access-log
