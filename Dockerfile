FROM python:3.11-slim-bullseye

WORKDIR /app

# Install FFmpeg dari repo official (lebih kecil)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -fsSL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz | tar -xJ \
    && mv ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ \
    && mv ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ \
    && rm -rf ffmpeg-*-amd64-static \
    && apt-get remove -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first untuk caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy hanya file yang diperlukan
COPY auto_subtitle_injector_full.py .

# Run the application
CMD ["python", "auto_subtitle_injector_full.py"]
