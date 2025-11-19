"""
Auto Subtitle Injector - Real Time Injector & HLS Soft Subtitle
Dibuat untuk Stabilitas dan Subtitle Real-Time yang Cepat (Soft Subtitle VTT).
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import StreamingResponse 
import uvicorn, subprocess, aiofiles, uuid, os, asyncio, shlex, time
import shutil
import re 
from typing import Optional

# ========== SETUP DASAR ==========
app = FastAPI(title="Real Time Subtitle Injector - Full Features")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKDIR = os.path.join(BASE_DIR, "asr_work")
OUTDIR = os.path.join(BASE_DIR, "out")
HLS_DIR = os.path.join(BASE_DIR, "hls_output")
PREVIEW_DIR = os.path.join(BASE_DIR, "preview")

os.makedirs(WORKDIR, exist_ok=True)
os.makedirs(OUTDIR, exist_ok=True)
os.makedirs(HLS_DIR, exist_ok=True)
os.makedirs(PREVIEW_DIR, exist_ok=True)

# Mounting untuk HLS dan Preview, VTT akan menggunakan endpoint khusus /static
app.mount("/hls", StaticFiles(directory=HLS_DIR), name="hls")
app.mount("/preview", StaticFiles(directory=PREVIEW_DIR), name="preview")
# =================================

# Global state
processing_status = {}
active_tasks = {}
hls_processes = {}
subtitle_cache = {}

# Whisper model
try:
    import whisper
    ASR_BACKEND = 'whisper'
    whisper_model = whisper.load_model("small")
    print("✅ Whisper small model loaded successfully")
except Exception as e:
    print(f"❌ Whisper error: {e}")
    ASR_BACKEND = None
    whisper_model = None

# ---------------- UTILITIES (FFMPEG & FILE MANAGEMENT) ----------------

def run_cmd(cmd):
    """Run command menggunakan shell=True untuk stabilitas FFmpeg."""
    try:
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) 
        out, err = proc.communicate(timeout=60) # Timeout 60 detik
        return proc.returncode, out.decode(errors='ignore'), err.decode(errors='ignore')
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)

def extract_channel_name(url: str) -> str:
    """Ekstrak chname dari URL untuk penamaan file."""
    url = url.replace('&amp;', '&')
    match = re.search(r'chname=([^&]*)', url)
    if match:
        name = match.group(1)
        cleaned_name = re.sub(r'[^a-zA-Z0-9]', '_', name).strip('_').lower()
        if cleaned_name:
            return cleaned_name
    return "stream"

def format_time(seconds):
    """Format time untuk WebVTT."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:01d}:{minutes:02d}:{secs:06.3f}"

def create_vtt_cue(start, end, text):
    """Create VTT cue."""
    return f"{format_time(start)} --> {format_time(end)}\n{text}\n\n"

def safe_path_windows(path):
    """Convert path to safe format for FFmpeg commands."""
    if path.startswith(('http://', 'https://', 'rtmp://', 'rtsp://')):
        return f'"{path}"'
    path = os.path.normpath(path)
    return f'"{path}"'

def create_ass_header():
    """Create ASS header dengan styling default."""
    return """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,2,2,30,30,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
def create_ass_subtitle(text, start_time, end_time):
    """Create ASS subtitle format."""
    start_ass = f"{int(start_time//3600):01d}:{int((start_time%3600)//60):02d}:{start_time%60:05.2f}"
    end_ass = f"{int(end_time//3600):01d}:{int((end_time%3600)//60):02d}:{end_time%60:05.2f}"
    return f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}"
	
def check_ffmpeg():
	    """Check if FFmpeg is available."""
    try:
        rc, out, err = run_cmd("ffmpeg -version")
        if rc == 0:
            print("✅ FFmpeg is available")
            return True
        else:
            print("❌ FFmpeg not found")
            return False
    except:
        print("❌ FFmpeg check failed")
        return False

# --- FUNGSI BARU: Test Stream Accessibility ---
def test_stream_accessibility(source_url: str):
    """Test apakah stream URL dapat diakses."""
    try:
        source_url = source_url.replace('&amp;', '&')
        
        # Test dengan ffprobe
        cmd = f'ffprobe -v quiet -print_format json -show_format -show_streams "{source_url}"'
        rc, out, err = run_cmd(cmd)
        
        if rc == 0:
            return True, "Stream is accessible"
        else:
            # Fallback: coba dengan timeout lebih pendek
            test_cmd = f'ffmpeg -t 5 -i "{source_url}" -f null - 2>&1'
            rc_test, out_test, err_test = run_cmd(test_cmd)
            
            if rc_test == 0 or "Video:" in err_test or "Audio:" in err_test:
                return True, "Stream is accessible (partial)"
            else:
                return False, f"Stream not accessible: {err_test}"
                
    except Exception as e:
        return False, f"Error testing stream: {str(e)}"

# --- FUNGSI HLS & PREVIEW (Optimized for Stability) ---

async def generate_preview_with_burned_subtitles(task_id: str, source_url: str, ass_path: str, preview_file_name: str, duration_seconds=30):
    """Generate preview video dengan burned-in subtitles."""
    preview_file = os.path.join(PREVIEW_DIR, preview_file_name)
    ffmpeg_input_options = ['-reconnect 1', '-reconnect_streamed 1', '-reconnect_delay_max 5', '-probesize 10M']
    
    try:
        source_url = source_url.replace('&amp;', '&')
        
        # Perintah Burned-in Subtitle
        cmd = (
            f'ffmpeg -y {" ".join(ffmpeg_input_options)} '
            f'-i "{source_url}" '
            f'-vf ass={safe_path_windows(ass_path)} '
            f'-t {str(duration_seconds)} '
            f'-c:v libx264 -preset fast -crf 23 '
            f'-c:a aac -b:a 128k '
            f'-movflags +faststart '
            f'{safe_path_windows(preview_file)}'
        )
        
        rc, out, err = run_cmd(cmd)

        if rc == 0 and os.path.exists(preview_file) and os.path.getsize(preview_file) > 10000:
            return preview_file_name
        
        # Fallback: tanpa subtitles
        fallback_cmd = (
            f'ffmpeg -y {" ".join(ffmpeg_input_options)} '
            f'-i "{source_url}" '
            f'-t {str(min(duration_seconds, 15))} '
            f'-c:v libx264 -preset fast -crf 23 '
            f'-c:a aac -b:a 128k '
            f'-movflags +faststart '
            f'{safe_path_windows(preview_file)}'
        )
        
        rc_fallback, out_fallback, err_fallback = run_cmd(fallback_cmd)

        if rc_fallback == 0 and os.path.exists(preview_file):
            return preview_file_name
        else:
            print(f"❌ Preview generation failed: {err_fallback}")
            return None
            
    except Exception as e:
        print(f"❌ Exception in preview generation: {e}")
        return None

async def generate_hls_stream_only(task_id: str, source_url: str):
    """Generate HLS stream TANPA burned-in subtitles (Soft Subtitle HLS)"""
    hls_output_dir = os.path.join(HLS_DIR, task_id)
    os.makedirs(hls_output_dir, exist_ok=True)
    m3u8_path = os.path.join(hls_output_dir, 'stream.m3u8')
    ffmpeg_input_options = ['-reconnect 1', '-reconnect_streamed 1', '-reconnect_delay_max 5']
    
    try:
        source_url = source_url.replace('&amp;', '&')
        
        cmd = (
            f'ffmpeg -y {" ".join(ffmpeg_input_options)} '
            f'-i "{source_url}" '
            f'-c:v libx264 -preset ultrafast -crf 28 '
            f'-c:a aac -b:a 96k '
            f'-f hls -hls_time 6 -hls_list_size 3 '
            f'-hls_flags delete_segments '
            f'-hls_segment_filename {safe_path_windows(os.path.join(hls_output_dir, "segment_%03d.ts"))} '
            f'{safe_path_windows(m3u8_path)}'
        )
        
        process = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        
        hls_processes[task_id] = process
        
        # Tunggu HLS stream siap (max 60 detik)
        for i in range(60): 
            if os.path.exists(m3u8_path):
                try:
                    with open(m3u8_path, 'r') as f:
                        content = f.read().strip()
                    if content and '#EXTM3U' in content:
                        return True
                except:
                    pass
            time.sleep(1)
        
        process.terminate()
        return False
        
    except Exception as e:
        print(f"❌ HLS stream error: {e}")
        return False

# --- WORKER UTAMA ---

async def generate_preview_and_update_status(task_id, source_url, ass_path, preview_file_name):
    """Worker asinkron untuk preview agar tidak memblokir ASR."""
    preview_file = await generate_preview_with_burned_subtitles(task_id, source_url, ass_path, preview_file_name)
    if preview_file and task_id in processing_status:
        processing_status[task_id]['preview_ready'] = True
        processing_status[task_id]['preview_file'] = preview_file
        processing_status[task_id]['message'] = 'Preview with subtitles ready!'
    elif task_id in processing_status:
        processing_status[task_id]['message'] = 'Preview generation failed, streaming continues.'

async def start_hls_background(task_id: str, source_url: str, ass_path: str):
    """Start HLS stream di background (menggunakan Soft Subtitle/Video Only)"""
    try:
        success = await generate_hls_stream_only(task_id, source_url) 
        
        if success:
            processing_status[task_id]['hls_ready'] = True
            processing_status[task_id]['hls_url'] = f'{task_id}/stream.m3u8'
            processing_status[task_id]['message'] = 'HLS stream with soft subtitles active!' 
        else:
            processing_status[task_id]['message'] = 'HLS unavailable - subtitles only'
    except Exception as e:
        print(f"❌ HLS background error: {e}")

async def realtime_subtitle_worker(task_id: str, source_url: str, segment_seconds=4):
    """Worker utama ASR dan file writer."""
    if not whisper_model:
        processing_status[task_id] = {
            'status': 'error',
            'error': 'Whisper model not available',
            'message': 'Whisper model failed to load'
        }
        return

    # --- PENAMAAN FILE DINAMIS ---
    channel_name = extract_channel_name(source_url)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base_filename = f"{channel_name}_{timestamp}_{task_id[:8]}" 
    
    vtt_file_name = f'{base_filename}.vtt'
    ass_file_name = f'{base_filename}.ass'
    preview_file_name = f'preview_{base_filename}.mp4'
    
    vtt_path = os.path.join(OUTDIR, vtt_file_name)
    ass_path = os.path.join(OUTDIR, ass_file_name)
    # -----------------------------

    # Inisialisasi file VTT dan ASS
    with open(vtt_path, 'w', encoding='utf-8') as f:
        f.write("WEBVTT\n\n")
        f.write("NOTE Real-time subtitles\n\n")
    
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(create_ass_header())
    
    processing_status[task_id] = {
        'status': 'streaming',
        'progress': 0,
        'current_segment': 0,
        'vtt_file': vtt_file_name,
        'ass_file': ass_file_name,
        'message': 'Starting real-time transcription...',
        'stream_url': source_url,
        'start_time': time.time(),
        'last_subtitle_time': 0,
        'total_subtitles': 0,
        'preview_ready': False,
        'hls_ready': False,
        'preview_file': None,
        'hls_url': None
    }
    
    subtitle_cache[task_id] = []
    seq = 0
    stream_start_time = time.time()
    
    try:
        await asyncio.sleep(2)
        
        while processing_status[task_id]['status'] == 'streaming':
            try:
                current_stream_time = time.time() - stream_start_time
                clip_file = os.path.join(WORKDIR, f'clip_{task_id}_{seq}.wav')
                
                # Command Audio Capture
                cmd = (
                    f'ffmpeg -y -loglevel error -i "{source_url}" '
                    f'-vn -ac 1 -ar 16000 -t {segment_seconds} '
                    f'-acodec pcm_s16le -f wav "{clip_file}"'
                )
                
                # --- INISIALISASI VARIABEL SEBELUM DIGUNAKAN ---
                start_capture_time = time.time() 
                # -------------------------------------------------------
                
                rc, out, err = run_cmd(cmd)
                
                if rc != 0:
                    await asyncio.sleep(1)
                    continue

                # Transcribe & Write Subtitles
                result = whisper_model.transcribe(clip_file)
                text = result.get('text', '').strip()

                if text and len(text) > 2:
                    processing_time = time.time() - start_capture_time 
                    subtitle_start = current_stream_time + processing_time
                    subtitle_end = subtitle_start + 4.0
                    
                    # Simpan ke VTT file
                    vtt_cue = create_vtt_cue(subtitle_start, subtitle_end, text)
                    with open(vtt_path, 'a', encoding='utf-8') as f:
                        f.write(vtt_cue)
                    
                    # Simpan ke ASS file
                    ass_line = create_ass_subtitle(text, subtitle_start, subtitle_end)
                    with open(ass_path, 'a', encoding='utf-8') as f:
                        f.write(ass_line + '\n')
                    
                    # Cache untuk display
                    subtitle_data = {
                        'start': subtitle_start, 'end': subtitle_end, 'text': text, 
                        'seq': seq, 'current_time': current_stream_time, 
                        'processing_time': processing_time
                    }
                    subtitle_cache[task_id].append(subtitle_data)
                    
                    if len(subtitle_cache[task_id]) > 20:
                        subtitle_cache[task_id].pop(0)
                    
                    processing_status[task_id]['last_subtitle_time'] = subtitle_start
                    processing_status[task_id]['total_subtitles'] += 1
                    
                    # Generate preview (Delayed, Asynchronous)
                    if (not processing_status[task_id]['preview_ready'] and 
                        processing_status[task_id]['total_subtitles'] >= 3):
                        
                        asyncio.create_task(
                            generate_preview_and_update_status(task_id, source_url, ass_path, preview_file_name)
                        )
                    
                    # Start HLS (Soft Subtitle)
                    if (not processing_status[task_id]['hls_ready'] and 
                        processing_status[task_id]['total_subtitles'] >= 5):
                        
                        asyncio.create_task(start_hls_background(task_id, source_url, ass_path))
                
                # Cleanup
                if os.path.exists(clip_file):
                    os.remove(clip_file)
                
                # Update status
                processing_status[task_id]['current_segment'] = seq
                processing_status[task_id]['progress'] = min(95, (seq % 100) + 1)
                
                status_msg = f'Processing - {seq} segments, {processing_status[task_id]["total_subtitles"]} subtitles'
                if processing_status[task_id]['preview_ready']:
                    status_msg += ' | 📹 Preview Ready'
                if processing_status[task_id]['hls_ready']:
                    status_msg += ' | 📡 HLS Streaming (Soft Sub)'
                
                processing_status[task_id]['message'] = status_msg
                seq += 1
                
                # Adaptive sleep
                elapsed = time.time() - start_capture_time
                sleep_time = max(0.1, segment_seconds - elapsed)
                await asyncio.sleep(sleep_time)
                
            except Exception as ex:
                await asyncio.sleep(1)
                
    except Exception as ex:
        processing_status[task_id]['status'] = 'error'
        processing_status[task_id]['error'] = str(ex)
        processing_status[task_id]['message'] = f'Error: {str(ex)}'
    finally:
        if task_id in subtitle_cache: del subtitle_cache[task_id]
        if task_id in hls_processes:
            try: hls_processes[task_id].terminate()
            except: pass

# ---------- API MODELS ----------
class StartRequest(BaseModel):
    source_url: str
    segment_seconds: int = 4
    whisper_model: str = 'small'

class TestStreamRequest(BaseModel):
    source_url: str

# ---------------- ROUTES (FFMPEG & SOFT SUBTITLE) ----------------
@app.get("/")
async def index():
    # Frontend HTML/JS disajikan di sini
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Real-Time Subtitle Injector - Full Features</title>
    <link href="https://vjs.zencdn.net/7.20.3/video-js.css" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; background: white; border-radius: 15px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); overflow: hidden; }
        .header { background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%); color: white; padding: 25px; text-align: center; }
        .controls { padding: 25px; background: #f8f9fa; border-bottom: 1px solid #e9ecef; }
        .input-group { margin-bottom: 20px; }
        .url-input { width: 100%; padding: 14px; border: 2px solid #e1e5e9; border-radius: 8px; font-size: 16px; }
        .config-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .config-item { display: flex; flex-direction: column; }
        .config-select { padding: 10px; border: 2px solid #e1e5e9; border-radius: 6px; font-size: 14px; }
        .button-group { display: flex; gap: 15px; flex-wrap: wrap; }
        .btn { padding: 14px 28px; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.3s ease; display: flex; align-items: center; gap: 8px; }
        .btn-primary { background: linear-gradient(135deg, #3498db 0%, #2980b9 100%); color: white; }
        .btn-stop { background: #e74c3c; color: white; }
        .btn-success { background: linear-gradient(135deg, #27ae60 0%, #229954 100%); color: white; }
        .status-panel { padding: 20px; background: white; margin: 0 25px 25px; border-radius: 10px; border-left: 5px solid #3498db; }
        .status-processing { border-left-color: #f39c12; background: #fff9e6; }
        .status-error { border-left-color: #e74c3c; background: #fdeaea; }
        .status-stopped { border-left-color: #95a5a6; background: #f8f9fa; }
        .video-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 25px; padding: 0 25px 25px; }
        .video-player { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 8px 25px rgba(0,0,0,0.1); }
        .video-js { border-radius: 8px; overflow: hidden; width: 100%; height: 300px; }
        .video-info { background: #f8f9fa; border-radius: 8px; padding: 15px; margin-top: 15px; font-size: 0.9em; }
        .info-success { background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }
        .subtitle-panel { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 8px 25px rgba(0,0,0,0.1); display: flex; flex-direction: column; }
        .subtitle-list { max-height: 400px; overflow-y: auto; border: 1px solid #e9ecef; border-radius: 6px; padding: 10px; margin-bottom: 15px; }
        .subtitle-item { padding: 10px 12px; margin: 5px 0; background: white; border-radius: 6px; border-left: 4px solid #3498db; }
        .subtitle-time { font-size: 0.8em; color: #7f8c8d; margin-bottom: 4px; }
        .subtitle-text { font-size: 0.95em; line-height: 1.4; }
        .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 15px; }
        .stat-item { text-align: center; padding: 10px; background: #f8f9fa; border-radius: 6px; }
        .stat-value { font-size: 1.2em; font-weight: bold; color: #2c3e50; }
        .stat-label { font-size: 0.8em; color: #7f8c8d; }
        .output-links { display: flex; gap: 10px; margin-top: 15px; flex-wrap: wrap; }
        .output-link { padding: 8px 16px; background: #3498db; color: white; text-decoration: none; border-radius: 6px; font-size: 0.9em; transition: all 0.3s ease; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎬 Real-Time Subtitle Injector</h1>
            <p>Live streaming dengan Preview Video & HLS Output</p>
        </div>
        
        <div class="controls">
            <div class="input-group">
                <label>🔗 Live Stream URL</label>
                <input type="text" id="streamUrl" class="url-input" 
                        placeholder="Masukkan URL live stream (HLS/m3u8 atau video file)"
                        value="https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_1MB.mp4">
            </div>
            
            <div class="config-grid">
                <div class="config-item">
                    <label>⏱️ Segment Duration</label>
                    <select id="segmentSeconds" class="config-select">
                        <option value="2">2 seconds (Fast)</option>
                        <option value="4" selected>4 seconds (Balanced)</option>
                        <option value="6">6 seconds (Accurate)</option>
                    </select>
                </div>
                
                <div class="config-item">
                    <label>🤖 Whisper Model</label>
                    <select id="whisperModel" class="config-select">
                        <option value="tiny">Tiny (Fastest)</option>
                        <option value="base">Base (Fast)</option>
                        <option value="small" selected>Small (Balanced)</option>
                    </select>
                </div>
            </div>
            
            <div class="button-group">
                <button id="startBtn" class="btn btn-primary">
                    <span>🚀 Start Processing</span>
                </button>
                <button id="stopBtn" class="btn btn-stop" disabled>
                    <span>⏹️ Stop</span>
                </button>
                <button id="testStreamBtn" class="btn btn-success">
                    <span>🔍 Test Stream</span>
                </button>
            </div>
        </div>
        
        <div id="status" class="status-panel">
            <div style="text-align: center; color: #7f8c8d;">
                <p>Ready to start real-time subtitle processing with Whisper Small model</p>
            </div>
        </div>

        <div class="video-grid">
            <div class="video-player">
                <h3>🎥 Original Stream + Soft Subtitles</h3>
                <video-js id="videoPlayerOriginal" class="vjs-default-skin vjs-big-play-centered" 
                            controls preload="auto" playsinline width="640" height="300">
                    <p class="vjs-no-js">
                        To view this video please enable JavaScript, and consider upgrading to a web browser that supports HTML5 video
                    </p>
                </video-js>
                <div class="video-info" id="originalInfo">
                    <strong>Original stream</strong> dengan VTT soft subtitles
                </div>
                <div class="stats">
                    <div class="stat-item">
                        <div class="stat-value" id="currentTime">0.0s</div>
                        <div class="stat-label">Current Time</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="subtitleCount">0</div>
                        <div class="stat-label">Total Subtitles</div>
                    </div>
                </div>
            </div>
            
            <div class="video-player">
                <h3>📹 Preview Video (Burned-in Subtitles)</h3>
                <video-js id="videoPlayerPreview" class="vjs-default-skin vjs-big-play-centered" 
                            controls preload="auto" playsinline width="640" height="300">
                    <p class="vjs-no-js">
                        To view this video please enable JavaScript, and consider upgrading to a web browser that supports HTML5 video
                    </p>
                </video-js>
                <div class="video-info" id="previewInfo">
                    <strong>Preview video</strong> dengan burned-in subtitles akan muncul di sini
                </div>
                <div class="output-links" id="previewLinks" style="display: none;">
                    <a href="#" id="previewDownload" class="output-link" target="_blank">📥 Download Preview</a>
                    <a href="#" id="previewView" class="output-link" target="_blank">👀 View Preview</a>
                </div>
            </div>
            
            <div class="video-player">
                <h3>📡 HLS Live Stream Output</h3>
                <video-js id="videoPlayerHLS" class="vjs-default-skin vjs-big-play-centered" 
                            controls preload="auto" playsinline width="640" height="300">
                    <p class="vjs-no-js">
                        To view this video please enable JavaScript, and consider upgrading to a web browser that supports HTML5 video
                    </p>
                </video-js>
                <div class="video-info" id="hlsInfo">
                    <strong>HLS stream</strong> dengan burned-in subtitles akan muncul di sini
                </div>
                <div class="output-links" id="hlsLinks" style="display: none;">
                    <a href="#" id="hlsStream" class="output-link" target="_blank">📡 HLS Stream URL</a>
                    <a href="#" id="hlsEmbed" class="output-link" target="_blank">🔗 Embed Code</a>
                </div>
            </div>
            
            <div class="subtitle-panel">
                <h3>📝 Live Subtitle Monitor</h3>
                <div class="subtitle-list" id="subtitleHistory">
                    <div style="text-align: center; padding: 20px; color: #7f8c8d;">
                        Subtitle history will appear here...
                    </div>
                </div>
                
                <div class="stats">
                    <div class="stat-item">
                        <div class="stat-value" id="segmentsProcessed">0</div>
                        <div class="stat-label">Segments</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="lastSubtitleTime">0.0s</div>
                        <div class="stat-label">Last Subtitle</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://vjs.zencdn.net/7.20.3/video.min.js"></script>
    <script>
        // =========================================================
        // POSISI 1: INISIALISASI PLAYER DAN VARIABEL GLOBAL
        // =========================================================
        const videoPlayerOriginal = videojs('videoPlayerOriginal', {
            fluid: true, responsive: true, playbackRates: [0.5, 1, 1.25, 1.5, 2]
        });
        
        const videoPlayerPreview = videojs('videoPlayerPreview', {
            fluid: true, responsive: true, playbackRates: [0.5, 1, 1.25, 1.5, 2]
        });
        
        const videoPlayerHLS = videojs('videoPlayerHLS', {
            fluid: true, responsive: true, playbackRates: [0.5, 1, 1.25, 1.5, 2]
        });
        
        let currentTaskId = null;
        let statusInterval = null;
        let subtitleInterval = null;
        let currentVttFile = null;

        // =========================================================
        // POSISI 2: DEFINISI SEMUA FUNGSI
        // =========================================================
        
        function formatTime(seconds) {
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return mins + ':' + secs.toString().padStart(2, '0');
        }
        
        // FUNGSI BARU: Memaksa refresh VTT (Soft Subtitle)
        function forceVttTrackRefresh(vttFile, videoPlayer) {
            const tracks = videoPlayer.remoteTextTracks() || [];
            for (let i = 0; i < tracks.length; i++) {
                 if (tracks[i].label === 'Auto Generated') {
                    videoPlayer.removeRemoteTextTrack(tracks[i]);
                    break; 
                }
            }

            videoPlayer.addRemoteTextTrack({
                kind: 'subtitles',
                label: 'Auto Generated',
                language: 'id',
                src: '/static/' + vttFile + '?t=' + Date.now()
            }, true);
            
            videoPlayer.textTracks().tracks_.forEach(track => {
                if (track.label === 'Auto Generated') {
                    track.mode = 'showing';
                }
            });
        }
        
        function loadVttSubtitles(vttFile) {
            if (currentVttFile === vttFile) return;
            currentVttFile = vttFile;
            
            forceVttTrackRefresh(vttFile, videoPlayerOriginal);
            
            document.getElementById('originalInfo').className = 'video-info info-success';
            document.getElementById('originalInfo').innerHTML = '<strong>✅ Soft subtitles active:</strong> ' + vttFile;
        }
        
        function loadPreviewVideo(previewFile) {
            const previewUrl = '/preview/' + previewFile;
            videoPlayerPreview.reset();
            videoPlayerPreview.src({ src: previewUrl, type: 'video/mp4' });
            videoPlayerPreview.load();
            videoPlayerPreview.play().catch(e => console.log("Auto-play Preview blocked:", e));
            
            document.getElementById('previewInfo').className = 'video-info info-success';
            document.getElementById('previewInfo').innerHTML = '<strong>✅ Preview video loaded:</strong> ' + previewFile;
            
            document.getElementById('previewLinks').style.display = 'flex';
            document.getElementById('previewDownload').href = previewUrl;
            document.getElementById('previewView').href = previewUrl;
        }
        
        function loadHLSStream(hlsUrl, vttFile) { 
            const hlsStreamUrl = '/hls/' + hlsUrl;
            
            videoPlayerHLS.reset(); 
            videoPlayerHLS.src({ src: hlsStreamUrl, type: 'application/x-mpegURL' });
            
            if (vttFile) {
                 forceVttTrackRefresh(vttFile, videoPlayerHLS);
            }
            
            videoPlayerHLS.load();
            videoPlayerHLS.play().catch(e => console.log("Auto-play HLS blocked:", e));
            
            document.getElementById('hlsInfo').className = 'video-info info-success';
            document.getElementById('hlsInfo').innerHTML = '<strong>✅ HLS stream active</strong> - Live with Soft Subtitles (VTT)';
            
            document.getElementById('hlsLinks').style.display = 'flex';
            document.getElementById('hlsStream').href = hlsStreamUrl;
            document.getElementById('hlsEmbed').href = hlsStreamUrl;
        }

        async function checkSubtitles() {
            if (!currentTaskId) return;
            try {
                const resp = await fetch('/subtitles/' + currentTaskId);
                const subtitles = await resp.json();
                updateSubtitleHistory(subtitles);
            } catch (error) {
                console.error('Error checking subtitles:', error);
            }
        }
        
        function updateSubtitleHistory(subtitles) {
            const historyEl = document.getElementById('subtitleHistory');
            
            if (!subtitles || subtitles.length === 0) {
                 historyEl.innerHTML = '<div style="text-align: center; padding: 20px; color: #7f8c8d;">Waiting for subtitles...</div>';
                return;
            }
            
            const recentSubtitles = subtitles.slice(-10).reverse();
            historyEl.innerHTML = recentSubtitles.map(sub => 
                '<div class="subtitle-item">' +
                    '<div class="subtitle-time">' +
                        '[' + formatTime(sub.start) + ' - ' + formatTime(sub.end) + '] | Delay: ' + (sub.processing_time?.toFixed(1) || '?') + 's' +
                    '</div>' +
                    '<div class="subtitle-text">' + sub.text + '</div>' +
                '</div>'
            ).join('');
        }

        function updateStatus(status) {
            const statusEl = document.getElementById('status');
            statusEl.innerHTML = '';
            
            if (status.status === 'streaming') {
                statusEl.className = 'status-panel status-processing';
                
                let statusHTML = '<h3>🔄 ' + (status.message || 'Processing...') + '</h3>' +
                    '<div style="margin-top: 10px;">' +
                        '<strong>Segments Processed:</strong> ' + (status.current_segment || 0) + '<br>' +
                        '<strong>Total Subtitles:</strong> ' + (status.total_subtitles || 0) + '<br>' +
                        '<strong>Progress:</strong> ' + (status.progress || 0) + '%<br>' +
                        '<strong>Last Subtitle:</strong> ' + (status.last_subtitle_time ? status.last_subtitle_time.toFixed(1) + 's' : 'None yet') +
                    '</div>';
                
                if (status.preview_ready) {
                    statusHTML += '<div style="margin-top: 10px; color: #27ae60;">✅ Preview Video Ready</div>';
                }
                
                if (status.hls_ready) {
                    statusHTML += '<div style="margin-top: 5px; color: #e67e22;">📡 HLS Stream Active (Soft Sub)</div>';
                }
                
                statusEl.innerHTML = statusHTML;
                
                document.getElementById('segmentsProcessed').textContent = status.current_segment || 0;
                document.getElementById('subtitleCount').textContent = status.total_subtitles || 0;
                document.getElementById('lastSubtitleTime').textContent = status.last_subtitle_time ? status.last_subtitle_time.toFixed(1) + 's' : '0.0s';
                
                if (status.vtt_file) { 
                    loadVttSubtitles(status.vtt_file);
                }
                
                if (status.preview_ready && status.preview_file) {
                    loadPreviewVideo(status.preview_file);
                }
                
                if (status.hls_ready && status.hls_url) {
                    loadHLSStream(status.hls_url, status.vtt_file); 
                }
                
            } else if (status.status === 'error') {
                statusEl.className = 'status-panel status-error';
                statusEl.innerHTML = '<h3>❌ Error</h3><p>' + (status.error || 'Unknown error occurred') + '</p>';
                document.getElementById('startBtn').disabled = false;
                document.getElementById('stopBtn').disabled = true;
            } else if (status.status === 'stopped') {
                statusEl.className = 'status-panel status-stopped';
                statusEl.innerHTML = '<h3>⏹️ Processing Stopped</h3><p>' + (status.message || 'Processing has been stopped') + '</p>';
            } else {
                statusEl.className = 'status-panel';
                statusEl.innerHTML = '<div style="text-align: center; color: #7f8c8d;"><p>Ready to start real-time subtitle processing</p></div>';
            }
        }
        
        // =========================================================
        // POSISI 3: EVENT LISTENERS / LOGIKA UTAMA
        // =========================================================
        
        videoPlayerOriginal.on('timeupdate', () => {
            document.getElementById('currentTime').textContent = videoPlayerOriginal.currentTime().toFixed(1) + 's';
        });
        
        document.getElementById('testStreamBtn').onclick = async () => {
            const url = document.getElementById('streamUrl').value.trim();
            if (!url) { alert('Please enter a stream URL'); return; }
            try {
                const response = await fetch('/test_stream', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ source_url: url })
                });
                const result = await response.json();
                alert(result.message);
            } catch (error) {
                console.error('Stream test error:', error);
                alert('Error testing stream: ' + error.message);
            }
        };

        document.getElementById('startBtn').onclick = async () => {
            const url = document.getElementById('streamUrl').value.trim();
            const segmentSeconds = parseInt(document.getElementById('segmentSeconds').value);
            const whisperModel = document.getElementById('whisperModel').value;
            
            if (!url) { alert('Please enter a stream URL'); return; }
            
            try {
                let streamType = url.includes('.m3u8') ? 'application/x-mpegURL' : 'video/mp4';
                videoPlayerOriginal.reset();
                videoPlayerOriginal.src({ src: url, type: streamType });
                videoPlayerOriginal.load();
                videoPlayerOriginal.play().catch(e => console.log("Auto-play original blocked:", e));

                const response = await fetch('/start', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ source_url: url, segment_seconds: segmentSeconds, whisper_model: whisperModel })
                });
                const result = await response.json();

                if (result.status === 'started') {
                    currentTaskId = result.task_id;
                    document.getElementById('startBtn').disabled = true;
                    document.getElementById('stopBtn').disabled = false;
                    
                    if (statusInterval) clearInterval(statusInterval);
                    if (subtitleInterval) clearInterval(subtitleInterval);

                    statusInterval = setInterval(async () => {
                        try {
                            const statusResp = await fetch('/status/' + currentTaskId);
                            const status = await statusResp.json();
                            updateStatus(status);
                        } catch (error) {
                            console.error('Status polling error:', error);
                        }
                    }, 2000);
                    
                    subtitleInterval = setInterval(() => {
                        if (currentVttFile) {
                            forceVttTrackRefresh(currentVttFile, videoPlayerOriginal);
                            if (videoPlayerHLS.src() && videoPlayerHLS.src().includes('stream.m3u8')) {
                                forceVttTrackRefresh(currentVttFile, videoPlayerHLS);
                            }
                        }
                    }, 4000); 
                    
                } else {
                    alert('Failed to start: ' + (result.detail || JSON.stringify(result)));
                }
                
            } catch (error) {
                console.error('Start error:', error);
                alert('Error starting stream: ' + error.message);
                document.getElementById('startBtn').disabled = false;
            }
        };
        
        document.getElementById('stopBtn').onclick = async () => {
            if (currentTaskId) { await fetch('/stop/' + currentTaskId, { method: 'POST' }); }
            
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
            
            if (statusInterval) clearInterval(statusInterval);
            if (subtitleInterval) clearInterval(subtitleInterval);
            
            currentTaskId = null;
            currentVttFile = null;
            
            document.getElementById('subtitleHistory').innerHTML = '<div style="text-align: center; padding: 20px; color: #7f8c8d;">Subtitle history will appear here...</div>';
            document.getElementById('originalInfo').className = 'video-info';
            document.getElementById('originalInfo').innerHTML = '<strong>Original stream</strong> dengan VTT soft subtitles';
            document.getElementById('previewInfo').className = 'video-info';
            document.getElementById('previewInfo').innerHTML = '<strong>Preview video</strong> dengan burned-in subtitles akan muncul di sini';
            document.getElementById('hlsInfo').className = 'video-info';
            document.getElementById('hlsInfo').innerHTML = '<strong>HLS stream</strong> dengan burned-in subtitles akan muncul di sini';
            document.getElementById('previewLinks').style.display = 'none';
            document.getElementById('hlsLinks').style.display = 'none';
            
            videoPlayerOriginal.reset();
            videoPlayerPreview.reset();
            videoPlayerHLS.reset();
        };
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html)

@app.post("/start")
async def start_streaming(req: StartRequest, background: BackgroundTasks):
    if not ASR_BACKEND:
        return JSONResponse({"status": "error", "detail": "Whisper model not available"})
    
    task_id = str(uuid.uuid4())
    
    accessible, message = test_stream_accessibility(req.source_url)
    if not accessible:
        return JSONResponse({"status": "error", "detail": f"Stream not accessible: {message}"})
    
    background.add_task(realtime_subtitle_worker, task_id, req.source_url, req.segment_seconds)
    
    active_tasks[task_id] = {
        'source_url': req.source_url,
        'started_at': time.time()
    }
    
    return {"status": "started", "task_id": task_id}

@app.post("/stop/{task_id}")
async def stop_streaming(task_id: str):
    if task_id in processing_status:
        processing_status[task_id]['status'] = 'stopped'
    
    if task_id in active_tasks:
        del active_tasks[task_id]
    
    if task_id in hls_processes:
        try:
            hls_processes[task_id].terminate()
            del hls_processes[task_id]
        except:
            pass
    
    return {"status": "stopped", "task_id": task_id}

@app.post("/test_stream")
async def test_stream(request: TestStreamRequest):
    source_url = request.source_url
    if not source_url:
        return JSONResponse({"status": "error", "message": "No URL provided"})
    
    accessible, message = test_stream_accessibility(source_url)
    
    if accessible:
        return JSONResponse({"status": "success", "message": message})
    else:
        return JSONResponse({"status": "error", "message": message})

@app.get("/static/{filename:path}")
async def get_static_file(filename: str):
    """Endpoint untuk menyajikan file statis, terutama VTT dengan StreamingResponse."""
    file_path = os.path.join(OUTDIR, filename)

    if not os.path.exists(file_path):
         # Cek file preview
         preview_path = os.path.join(PREVIEW_DIR, filename)
         if os.path.exists(preview_path):
             return FileResponse(preview_path)

         raise HTTPException(status_code=404, detail="File not found")

    # PENANGANAN KHUSUS UNTUK FILE VTT (Soft Subtitle Real-Time)
    if filename.endswith(('.vtt', '.VTT')):
        
        async def file_iterator():
            async with aiofiles.open(file_path, mode="rb") as file:
                content = await file.read()
                yield content
        
        return StreamingResponse(
            file_iterator(),
            media_type="text/vtt",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Expires": "0"}
        )
    
    # Untuk file statis lainnya (.ass, dll di OUTDIR)
    return FileResponse(file_path)

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id in processing_status:
        return processing_status[task_id]
    else:
        return {"status": "not_found"}

@app.get("/subtitles/{task_id}")
async def get_subtitles(task_id: str):
    if task_id in subtitle_cache:
        return subtitle_cache[task_id]
    else:
        return []

@app.get("/hls/{task_id}/stream.m3u8")
async def get_hls_stream(task_id: str):
    """Serve HLS stream file."""
    m3u8_path = os.path.join(HLS_DIR, task_id, 'stream.m3u8')
    
    if not os.path.exists(m3u8_path):
        raise HTTPException(status_code=404, detail="HLS stream not found")
    
    return FileResponse(
        m3u8_path,
        media_type="application/vnd.apple.mpegurl",
        filename=f"stream_{task_id}.m3u8"
    )

@app.get("/hls/{task_id}/{segment_file}")
async def get_hls_segment(task_id: str, segment_file: str):
    """Serve HLS segment files dengan header anti-cache."""
    segment_path = os.path.join(HLS_DIR, task_id, segment_file)
    
    if not os.path.exists(segment_path):
        raise HTTPException(status_code=404, detail="Segment not found")
    
    return FileResponse(
        segment_path,
        media_type="video/MP2T",
        filename=segment_file,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )
    
@app.get("/cleanup/{task_id}")
async def cleanup_task(task_id: str):
    """Cleanup task resources."""
    if task_id in processing_status:
        del processing_status[task_id]
    if task_id in active_tasks:
        del active_tasks[task_id]
    if task_id in hls_processes:
        try:
            hls_processes[task_id].terminate()
            del hls_processes[task_id]
        except:
            pass
    
    # Cleanup files
    for dir_path in [WORKDIR, OUTDIR, HLS_DIR, PREVIEW_DIR]:
        task_files = [f for f in os.listdir(dir_path) if task_id in f]
        for file in task_files:
            try:
                os.remove(os.path.join(dir_path, file))
            except:
                pass
    
    return {"status": "cleaned", "task_id": task_id}

if __name__ == "__main__":
    print("\n" + "="*50)
    print("🚀 REAL-TIME SUBTITLE INJECTOR - FULL FEATURES")
    print("="*50)
    print(f"Whisper Status: {'✅ READY' if whisper_model else '❌ NOT AVAILABLE'}")
    print("="*50 + "\n")
    
    # Untuk production di Railway
	check_ffmpeg()
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)