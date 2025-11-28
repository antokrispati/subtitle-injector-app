FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir fastapi uvicorn

# Copy app
COPY main.py .

# Gunakan shell form untuk expand environment variable
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
