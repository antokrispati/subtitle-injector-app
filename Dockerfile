FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg && apt-get clean
RUN pip install --no-cache-dir fastapi uvicorn aiofiles ffmpeg-python openai-whisper

COPY auto_subtitle_injector_full.py .

CMD python -m uvicorn auto_subtitle_injector_full:app --host 0.0.0.0 --port $PORT
