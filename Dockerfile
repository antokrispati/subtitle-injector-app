# Gunakan base image Python yang ringan
FROM python:3.10-slim

# 1. Install System Dependencies (Wajib: FFmpeg & Git)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# 2. Copy requirements dan install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Pre-download Whisper Model 'small'
# Langkah ini penting agar model tersimpan di dalam image Docker
# dan tidak perlu didownload setiap kali aplikasi restart.
pip install torch ... --index-url .../cpu

# 4. Copy kode aplikasi
COPY auto_subtitle_injector_full.py .

# Buat folder output yang dibutuhkan
RUN mkdir -p asr_work out

# 5. Jalankan aplikasi
# CMD akan menjalankan script python yang sudah kita update port-nya
CMD ["python", "auto_subtitle_injector_full.py"]