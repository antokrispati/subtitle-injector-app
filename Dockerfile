# Gunakan Python versi ringan
FROM python:3.10-slim

# 1. Install System Dependencies (Wajib: FFmpeg & Git)
# Kita tambahkan --no-install-recommends agar instalasi linux lebih kecil
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. OPTIMASI KRITIS: Install PyTorch versi CPU (Hanya ~200MB)
# Kita jalankan ini DULUAN agar build lebih cepat dan hemat memori
# Perintah ini sekarang SUDAH BENAR (menggunakan RUN pip install)
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# 3. Install Whisper (akan menggunakan torch CPU yang sudah terinstal)
RUN pip install openai-whisper

# 4. Install dependency lainnya dari requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Pre-download model Whisper 'small' agar start-up cepat
RUN python -c "import whisper; print('Downloading model...'); whisper.load_model('small')"

# 6. Copy kode aplikasi
COPY auto_subtitle_injector_full.py .

# Buat folder output yang dibutuhkan
RUN mkdir -p asr_work out hls_output preview

# 7. Jalankan aplikasi
# CMD akan menjalankan script python utama
CMD ["python", "auto_subtitle_injector_full.py"]