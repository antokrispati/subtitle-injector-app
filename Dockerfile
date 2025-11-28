# Gunakan Python versi ringan
FROM python:3.10-slim

# 1. OPTIMASI LOGGING: Wajib untuk Cloud (Railway/GCloud)
# Mencegah log Python tertahan (buffered), mempermudah debugging jika crash
ENV PYTHONUNBUFFERED=1

# 2. Install System Dependencies (Wajib: FFmpeg, Git, DAN CURL)
# Menambahkan 'curl' karena wajib untuk Health Check di Railway/Cloud
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 3. OPTIMASI KRITIS: Install PyTorch versi CPU (Hanya ~200MB)
# Kita jalankan ini DULUAN agar build lebih cepat dan hemat memori
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# 4. Install Whisper (akan menggunakan torch CPU yang sudah terinstal)
RUN pip install openai-whisper

# 5. Install dependency lainnya dari requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Pre-download model Whisper 'tiny' (Lebih Ringan)
# MENGGANTI 'small' ke 'tiny' agar tidak crash di server dengan RAM kecil (512MB)
RUN python -c "import whisper; print('Downloading model...'); whisper.load_model('tiny')"

# 7. Copy kode aplikasi
COPY auto_subtitle_injector_full.py .

# Buat folder output yang dibutuhkan
RUN mkdir -p asr_work out hls_output preview

# 8. Jalankan aplikasi
# CMD akan menjalankan script python utama
CMD ["python", "auto_subtitle_injector_full.py"]