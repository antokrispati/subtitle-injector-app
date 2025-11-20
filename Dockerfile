FROM python:3.11-slim

WORKDIR /app

# Install system dependencies untuk Whisper + FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Pre-download Whisper model kecil saat build (opsional)
RUN python -c "import whisper; whisper.load_model('small')"

# Run app
CMD python -m uvicorn auto_subtitle_injector_full.py:app --host 0.0.0.0 --port $PORT
