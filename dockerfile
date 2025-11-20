# Gunakan base image yang lebih ringan
FROM python:3.11-slim-bullseye

# Set working directory
WORKDIR /app

# Copy requirements terlebih dahulu untuk caching
COPY requirements.txt .

# Install dependencies system dan Python
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy aplikasi
COPY . .

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH"
