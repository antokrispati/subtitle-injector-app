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

# Perbaikan dari log yang ada:
RUN python -m venv /opt/venv  # bukan "python -n venv"
RUN pip install --upgrade pip  # bukan "--ungrade"

# Buat .dockerignore file
.git
__pycache__
*.pyc
*.pyo
*.pyd
.Python
env
pip-log.txt
.DS_Store
README.md
test/
tests/
.coverage
.cache

# Build stage
FROM python:3.11-slim as builder
WORKDIR /app
COPY requirements.txt
RUN pip install --user -r requirements.txt

# Final stage
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .
ENV PATH=/root/.local/bin:$PATH
