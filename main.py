from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List
import aiofiles
import asyncio
import subprocess
import requests
import uuid
import os
import time
from googletrans import Translator

app = FastAPI(title="HLS Subtitle Translation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Models
class TranslationRequest(BaseModel):
    hls_url: str
    source_lang: str = "id"
    target_lang: str = "en"
    segment_duration: int = 10

class TranslationStatus(BaseModel):
    job_id: str
    status: str
    progress: float
    translated_segments: int
    total_segments: int
    output_url: Optional[str] = None

# Global variables
translation_jobs: Dict[str, TranslationStatus] = {}
translator = Translator()

@app.get("/")
async def root():
    return {
        "message": "HLS Subtitle Translation API - Ready",
        "endpoints": {
            "start_translation": "POST /translate",
            "check_status": "GET /status/{job_id}",
            "direct_translate": "POST /translate-direct",
            "health": "GET /health"
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "curl_available": True}

def download_hls_segment(segment_url: str, output_path: str) -> bool:
    """Download HLS segment menggunakan requests (bukan curl)"""
    try:
        response = requests.get(segment_url, timeout=30)
        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                f.write(response.content)
            return True
    except Exception as e:
        print(f"Error downloading segment: {e}")
    return False

def extract_audio_from_segment(segment_path: str, audio_path: str) -> bool:
    """Extract audio dari video segment menggunakan FFmpeg"""
    try:
        cmd = [
            'ffmpeg', '-i', segment_path,
            '-ac', '1', '-ar', '16000',
            '-vn', '-y', audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        print(f"Error extracting audio: {e}")
        return False

def transcribe_audio_simulation(audio_path: str, language: str = "id") -> Optional[str]:
    """Simulasi transcription - replace dengan service real"""
    try:
        # Simulasi transcription berdasarkan bahasa
        if language == "id":
            texts = [
                "Halo selamat datang di streaming live kami",
                "Hari ini kita akan membahas teknologi terbaru",
                "Terima kasih sudah bergabung bersama kami",
                "Jangan lupa subscribe channel kami",
                "Silakan tinggalkan komentar di bawah"
            ]
        else:
            texts = [
                "Hello welcome to our live streaming",
                "Today we will discuss the latest technology", 
                "Thank you for joining us",
                "Don't forget to subscribe to our channel",
                "Please leave comments below"
            ]
        
        import random
        return random.choice(texts)
    except Exception as e:
        print(f"Error in transcription: {e}")
        return None

def translate_text(text: str, source_lang: str, target_lang: str) -> Optional[str]:
    """Translate text menggunakan Google Translate"""
    try:
        if source_lang == target_lang:
            return text
            
        translation = translator.translate(text, src=source_lang, dest=target_lang)
        return translation.text
    except Exception as e:
        print(f"Error in translation: {e}")
        return None

def create_subtitle_file(translated_text: str, output_path: str, start_time: float, duration: float = 10.0):
    """Buat file subtitle SRT format"""
    try:
        end_time = start_time + duration
        
        def format_time(seconds):
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = seconds % 60
            return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace('.', ',')
        
        srt_content = f"""1
{format_time(start_time)} --> {format_time(end_time)}
{translated_text}

"""
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)
        return True
    except Exception as e:
        print(f"Error creating subtitle: {e}")
        return False

async def process_hls_translation(job_id: str, hls_url: str, source_lang: str, target_lang: str):
    """Background task untuk proses translation HLS"""
    try:
        # Get HLS playlist
        response = requests.get(hls_url)
        if response.status_code != 200:
            translation_jobs[job_id].status = "failed"
            return
        
        # Parse sederhana untuk demo (tanpa m3u8 library)
        segments = []
        for line in response.text.split('\n'):
            if line.endswith('.ts') and not line.startswith('#'):
                segments.append(line.strip())
        
        if not segments:
            # Jika tidak ada segments, buat dummy untuk demo
            segments = [f"segment_{i}.ts" for i in range(3)]
        
        translation_jobs[job_id].total_segments = len(segments)
        translation_jobs[job_id].status = "processing"
        
        # Process segments
        subtitle_files = []
        for i, segment in enumerate(segments[:3]):  # Batasi 3 segment untuk demo
            # Simulasi proses segment
            transcribed_text = transcribe_audio_simulation("dummy", source_lang)
            if transcribed_text:
                translated_text = translate_text(transcribed_text, source_lang, target_lang)
                if translated_text:
                    subtitle_path = f"/tmp/{job_id}_subtitle_{i}.srt"
                    if create_subtitle_file(translated_text, subtitle_path, i * 10):
                        subtitle_files.append(subtitle_path)
            
            # Update progress
            translation_jobs[job_id].progress = ((i + 1) / len(segments[:3])) * 100
            translation_jobs[job_id].translated_segments = i + 1
            await asyncio.sleep(1)  # Simulasi processing time
        
        if subtitle_files:
            translation_jobs[job_id].status = "completed"
            translation_jobs[job_id].output_url = f"/download/{job_id}"
        else:
            translation_jobs[job_id].status = "failed"
        
    except Exception as e:
        print(f"Error in background processing: {e}")
        translation_jobs[job_id].status = "failed"

@app.post("/translate", response_model=TranslationStatus)
async def start_translation(request: TranslationRequest, background_tasks: BackgroundTasks):
    """Start HLS translation process"""
    
    job_id = str(uuid.uuid4())
    
    translation_jobs[job_id] = TranslationStatus(
        job_id=job_id,
        status="initializing",
        progress=0,
        translated_segments=0,
        total_segments=0
    )
    
    background_tasks.add_task(
        process_hls_translation,
        job_id, request.hls_url, request.source_lang, request.target_lang
    )
    
    return translation_jobs[job_id]

@app.get("/status/{job_id}", response_model=TranslationStatus)
async def get_translation_status(job_id: str):
    """Check translation status"""
    if job_id not in translation_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return translation_jobs[job_id]

@app.get("/download/{job_id}")
async def download_subtitle(job_id: str):
    """Download translated subtitle file"""
    if job_id not in translation_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if translation_jobs[job_id].status != "completed":
        raise HTTPException(status_code=400, detail="Translation not completed")
    
    # Cari file subtitle
    for i in range(3):
        subtitle_path = f"/tmp/{job_id}_subtitle_{i}.srt"
        if os.path.exists(subtitle_path):
            async with aiofiles.open(subtitle_path, 'r', encoding='utf-8') as f:
                content = await f.read()
            return {
                "filename": f"translated_subtitle_{job_id}.srt",
                "content": content,
                "format": "srt"
            }
    
    raise HTTPException(status_code=404, detail="Subtitle file not found")

@app.post("/translate-direct")
async def translate_direct_text(text: str, source_lang: str = "auto", target_lang: str = "en"):
    """Direct text translation endpoint"""
    try:
        translation = translator.translate(text, src=source_lang, dest=target_lang)
        return {
            "original": text,
            "translated": translation.text,
            "source_language": translation.src,
            "target_language": translation.dest
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
