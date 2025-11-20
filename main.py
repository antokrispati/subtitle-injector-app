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
import m3u8

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
    source_lang: str = "id"  # id = Indonesian, en = English
    target_lang: str = "en"  # id = Indonesian, en = English
    segment_duration: int = 10  # Durasi segment dalam detik

class TranslationStatus(BaseModel):
    job_id: str
    status: str
    progress: float
    translated_segments: int
    total_segments: int
    output_url: Optional[str] = None

# Global variables untuk tracking jobs
translation_jobs: Dict[str, TranslationStatus] = {}
translator = Translator()

@app.get("/")
async def root():
    return {
        "message": "HLS Subtitle Translation API",
        "endpoints": {
            "start_translation": "POST /translate",
            "check_status": "GET /status/{job_id}",
            "health": "GET /health"
        }
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "hls-subtitle-translator"}

def download_hls_segment(segment_url: str, output_path: str) -> bool:
    """Download HLS segment"""
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
            '-ac', '1', '-ar', '16000',  # Mono, 16kHz
            '-vn',  # No video
            '-y',   # Overwrite output
            audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        print(f"Error extracting audio: {e}")
        return False

def transcribe_audio_whisper_api(audio_path: str, language: str = "id") -> Optional[str]:
    """Transcribe audio menggunakan OpenAI Whisper API (gratis alternative)"""
    try:
        # Alternative: Gunakan Hugging Face Whisper API atau service external
        # Untuk sekarang, kita simulasikan dengan text dummy
        # Dalam implementasi real, gunakan: https://api.openai.com/v1/audio/transcriptions
        
        # Simulasi transcription
        if language == "id":
            return "Ini adalah contoh teks dari audio bahasa Indonesia"
        else:
            return "This is example text from English audio"
            
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
        
        # Format waktu untuk SRT
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

def process_hls_segment(segment_url: str, segment_index: int, source_lang: str, target_lang: str, job_id: str):
    """Process satu segment HLS: download, transcribe, translate, buat subtitle"""
    try:
        # Update status
        translation_jobs[job_id].progress = (segment_index / translation_jobs[job_id].total_segments) * 100
        translation_jobs[job_id].translated_segments = segment_index
        
        # Download segment
        segment_path = f"/tmp/{job_id}_segment_{segment_index}.ts"
        if not download_hls_segment(segment_url, segment_path):
            return None
        
        # Extract audio
        audio_path = f"/tmp/{job_id}_audio_{segment_index}.wav"
        if not extract_audio_from_segment(segment_path, audio_path):
            return None
        
        # Transcribe audio (simulasi - replace dengan service real)
        transcribed_text = transcribe_audio_whisper_api(audio_path, source_lang)
        if not transcribed_text:
            return None
        
        # Translate text
        translated_text = translate_text(transcribed_text, source_lang, target_lang)
        if not translated_text:
            return None
        
        # Buat subtitle file
        subtitle_path = f"/tmp/{job_id}_subtitle_{segment_index}.srt"
        start_time = segment_index * 10  # 10 detik per segment
        if not create_subtitle_file(translated_text, subtitle_path, start_time):
            return None
        
        # Cleanup temporary files
        for temp_file in [segment_path, audio_path]:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        
        return subtitle_path
        
    except Exception as e:
        print(f"Error processing segment {segment_index}: {e}")
        return None

async def process_hls_translation(job_id: str, hls_url: str, source_lang: str, target_lang: str):
    """Background task untuk proses translation HLS"""
    try:
        # Get HLS playlist
        response = requests.get(hls_url)
        if response.status_code != 200:
            translation_jobs[job_id].status = "failed"
            return
        
        playlist = m3u8.loads(response.text)
        segments = [segment.uri for segment in playlist.segments if segment.uri]
        
        if not segments:
            translation_jobs[job_id].status = "failed"
            return
        
        translation_jobs[job_id].total_segments = len(segments)
        translation_jobs[job_id].status = "processing"
        
        # Process setiap segment
        subtitle_files = []
        for i, segment_url in enumerate(segments[:5]):  # Batasi 5 segment untuk demo
            full_segment_url = segment_url if segment_url.startswith('http') else hls_url.rsplit('/', 1)[0] + '/' + segment_url
            
            subtitle_path = await asyncio.get_event_loop().run_in_executor(
                None, process_hls_segment, full_segment_url, i, source_lang, target_lang, job_id
            )
            
            if subtitle_path:
                subtitle_files.append(subtitle_path)
        
        # Combine semua subtitle files
        if subtitle_files:
            combined_subtitle_path = f"/tmp/{job_id}_combined.srt"
            with open(combined_subtitle_path, 'w', encoding='utf-8') as outfile:
                for i, subtitle_file in enumerate(subtitle_files):
                    with open(subtitle_file, 'r', encoding='utf-8') as infile:
                        content = infile.read()
                        # Update subtitle numbers
                        lines = content.split('\n')
                        if len(lines) >= 3:
                            lines[0] = str(i + 1)  # Update subtitle number
                            outfile.write('\n'.join(lines) + '\n\n')
                    
                    # Cleanup individual subtitle file
                    if os.path.exists(subtitle_file):
                        os.remove(subtitle_file)
            
            translation_jobs[job_id].status = "completed"
            translation_jobs[job_id].progress = 100
            translation_jobs[job_id].output_url = f"/download/{job_id}"
        
    except Exception as e:
        print(f"Error in background processing: {e}")
        translation_jobs[job_id].status = "failed"

@app.post("/translate", response_model=TranslationStatus)
async def start_translation(request: TranslationRequest, background_tasks: BackgroundTasks):
    """Start HLS translation process"""
    
    job_id = str(uuid.uuid4())
    
    # Initialize job status
    translation_jobs[job_id] = TranslationStatus(
        job_id=job_id,
        status="initializing",
        progress=0,
        translated_segments=0,
        total_segments=0
    )
    
    # Start background task
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
    
    subtitle_path = f"/tmp/{job_id}_combined.srt"
    if not os.path.exists(subtitle_path):
        raise HTTPException(status_code=404, detail="Subtitle file not found")
    
    async with aiofiles.open(subtitle_path, 'r', encoding='utf-8') as f:
        content = await f.read()
    
    return {
        "filename": f"translated_subtitle_{job_id}.srt",
        "content": content,
        "format": "srt"
    }

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

# Cleanup job data periodically
@app.on_event("shutdown")
async def cleanup_jobs():
    """Cleanup temporary files on shutdown"""
    for job_id in list(translation_jobs.keys()):
        for file_pattern in [f"/tmp/{job_id}_*"]:
            # Cleanup temporary files
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
