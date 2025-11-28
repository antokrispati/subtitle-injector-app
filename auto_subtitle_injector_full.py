"""
Auto Subtitle Injector - Real Time dengan Video Preview, HLS Output, dan Translasi

Cara menjalankan:
  1. Install system deps: ffmpeg (Pastikan ada di PATH)
  2. Install Python deps:
       pip install fastapi uvicorn pydantic aiofiles python-multipart openai-whisper deep-translator requests
  3. Jalankan:
       uvicorn auto_subtitle_injector_full:app --host 0.0.0.0 --port 8000
  4. Buka di browser:
       http://localhost:8000
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn, subprocess, aiofiles, uuid, os, asyncio, shlex, time, shutil, requests
from deep_translator import GoogleTranslator

# ========== SETUP DASAR ==========
app = FastAPI(title="Real Time Subtitle Injector - Full Features + Translation")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gunakan path yang lebih sederhana tanpa spasi
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKDIR = os.path.join(BASE_DIR, "asr_work")
OUTDIR = os.path.join(BASE_DIR, "out")
HLS_DIR = os.path.join(BASE_DIR, "hls_output")
PREVIEW_DIR = os.path.join(BASE_DIR, "preview")

for d in [WORKDIR, OUTDIR, HLS_DIR, PREVIEW_DIR]:
    os.makedirs(d, exist_ok=True)

app.mount("/hls", StaticFiles(directory=HLS_DIR), name="hls")
app.mount("/preview", StaticFiles(directory=PREVIEW_DIR), name="preview")
app.mount("/static", StaticFiles(directory=OUTDIR), name="static")

# Global state
processing_status = {}
active_tasks = {}
hls_processes = {}
subtitle_cache = {}

# Variabel Global untuk Whisper (Lazy Load)
whisper_model = None
ASR_BACKEND = 'whisper'

# ---------- UTILITIES ----------
def get_or_load_model():
    """Fungsi Lazy Load: Memuat model hanya jika belum ada"""
    global whisper_model
    if whisper_model is None:
        try:
            import whisper
            print("⏳ Initializing Whisper Model (Tiny) for the first time...")
            # Gunakan 'tiny' untuk performa Cloud gratisan (Hemat RAM)
            whisper_model = whisper.load_model("tiny")
            print("✅ Whisper model loaded into memory!")
        except Exception as e:
            print(f"❌ Failed to load Whisper: {e}")
            return None
    return whisper_model

def run_cmd(cmd):
    """Run command dengan timeout"""
    try:
        # Timeout pendek untuk capture audio agar tidak blocking
        timeout_val = 15 if "hls" not in cmd else None # HLS process berjalan lama
        proc = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if timeout_val:
            out, err = proc.communicate(timeout=timeout_val)
            return proc.returncode, out.decode(errors='ignore'), err.decode(errors='ignore')
        else:
            return proc # Return process object untuk long running tasks
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)

def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:01d}:{minutes:02d}:{secs:06.3f}"

def create_vtt_cue(start, end, text):
    return f"{format_time(start)} --> {format_time(end)}\n{text}\n\n"

def create_ass_subtitle(text, start_time, end_time):
    """Create ASS subtitle format untuk burned-in subtitles"""
    start_ass = f"{int(start_time//3600):01d}:{int((start_time%3600)//60):02d}:{start_time%60:05.2f}"
    end_ass = f"{int(end_time//3600):01d}:{int((end_time%3600)//60):02d}:{end_time%60:05.2f}"
    safe_text = text.replace(",", "\\N") 
    return f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{safe_text}"

def create_ass_header():
    return """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,60,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,30,30,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def safe_path_windows(path):
    """Convert path to safe format for Windows FFmpeg commands"""
    if path.startswith(('http://', 'https://', 'rtmp://', 'rtsp://')):
        return f'"{path}"'
    path = os.path.abspath(path).replace('\\', '/')
    path = path.replace(':', '\\:')
    return f"'{path}'"

async def generate_preview_with_burned_subtitles(task_id: str, source_url: str, ass_path: str, duration_seconds=15):
    """Generate preview video pendek dengan burned-in subtitles"""
    preview_file = os.path.join(PREVIEW_DIR, f'preview_{task_id}.mp4')
    
    try:
        source_url = source_url.replace('&amp;', '&')
        ffmpeg_input = f'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -i "{source_url}"'
        
        print(f"🎬 Generating preview for task {task_id}")
        
        cmd = (
            f'ffmpeg -y {ffmpeg_input} '
            f'-vf "ass={safe_path_windows(ass_path)}" '
            f'-t {duration_seconds} '
            f'-c:v libx264 -preset ultrafast -crf 28 '
            f'-c:a aac -b:a 64k '
            f'-movflags +faststart '
            f'"{preview_file}"'
        )
        
        print(f"Preview Command: {cmd}")
        rc, out, err = run_cmd(cmd)
        
        if rc == 0 and os.path.exists(preview_file) and os.path.getsize(preview_file) > 1000:
            print(f"✅ Preview generated: {preview_file}")
            return f'preview_{task_id}.mp4'
        else:
            print(f"❌ Preview generation failed: {err}")
            return None
            
    except Exception as e:
        print(f"❌ Exception in preview generation: {e}")
        return None

async def start_hls_stream(task_id: str, source_url: str, ass_path: str):
    """Start proses HLS background dengan burned-in subtitles"""
    hls_output_dir = os.path.join(HLS_DIR, task_id)
    os.makedirs(hls_output_dir, exist_ok=True)
    
    m3u8_path = os.path.join(hls_output_dir, 'stream.m3u8')
    seg_path = os.path.join(hls_output_dir, 'segment_%03d.ts')
    
    try:
        source_url = source_url.replace('&amp;', '&')
        print(f"📡 Starting HLS stream process...")
        
        cmd = [
            'ffmpeg', '-y',
            '-re', 
            '-i', source_url,
            '-vf', f"ass={safe_path_windows(ass_path)}",
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', '-g', '60',
            '-c:a', 'aac', '-b:a', '96k',
            '-f', 'hls', 
            '-hls_time', '4', 
            '-hls_list_size', '5',
            '-hls_flags', 'delete_segments',
            '-hls_allow_cache', '0',
            '-hls_segment_filename', seg_path,
            m3u8_path
        ]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        
        hls_processes[task_id] = process
        print(f"✅ HLS process started (PID: {process.pid})")
        return True
        
    except Exception as e:
        print(f"❌ HLS stream start error: {e}")
        return False

# ---------- REAL-TIME WORKER ----------
async def realtime_subtitle_worker(task_id: str, source_url: str, source_lang: str, target_lang: str):
    # Lazy load model saat worker pertama kali dijalankan
    model = get_or_load_model()
    if not model:
        print("❌ Cannot start worker: Whisper model failed to load")
        return

    # Setup Files
    vtt_path = os.path.join(OUTDIR, f'subtitles_{task_id}.vtt')
    ass_path = os.path.join(OUTDIR, f'subtitles_{task_id}.ass')
    
    with open(vtt_path, 'w', encoding='utf-8') as f:
        f.write("WEBVTT\n\n")
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(create_ass_header())
    
    # Init Status
    processing_status[task_id] = {
        'status': 'streaming',
        'progress': 0,
        'current_segment': 0,
        'last_subtitle': '',
        'start_time': time.time(),
        'preview_ready': False,
        'hls_ready': False,
        'preview_file': None,
        'hls_url': None
    }
    
    subtitle_cache[task_id] = []
    seq = 0
    
    translator = None
    if target_lang != 'original':
        translator = GoogleTranslator(source='auto', target=target_lang)

    hls_started = await start_hls_stream(task_id, source_url, ass_path)
    if hls_started:
        processing_status[task_id]['hls_ready'] = True
        processing_status[task_id]['hls_url'] = f'/hls/{task_id}/stream.m3u8'

    stream_start_time = time.time()
    segment_duration = 5 

    while task_id in active_tasks:
        try:
            current_stream_time = time.time() - stream_start_time
            clip_file = os.path.join(WORKDIR, f'clip_{task_id}_{seq}.wav')
            
            cmd = (
                f'ffmpeg -y -hide_banner -loglevel error '
                f'-i "{source_url}" '
                f'-t {segment_duration} '
                f'-vn -ac 1 -ar 16000 -acodec pcm_s16le -f wav "{clip_file}"'
            )
            
            start_proc = time.time()
            rc, out, err = run_cmd(cmd)
            
            if rc == 0 and os.path.exists(clip_file) and os.path.getsize(clip_file) > 1000:
                decode_options = {}
                if source_lang != 'auto':
                    decode_options['language'] = source_lang

                # Transcribe menggunakan model yang sudah diload
                result = model.transcribe(clip_file, fp16=False, **decode_options)
                text_original = result.get('text', '').strip()

                if text_original:
                    final_text = text_original
                    if translator:
                        try:
                            final_text = translator.translate(text_original)
                        except: pass

                    process_duration = time.time() - start_proc
                    sub_start = current_stream_time
                    sub_end = sub_start + segment_duration
                    
                    vtt_cue = create_vtt_cue(sub_start, sub_end, final_text)
                    with open(vtt_path, 'a', encoding='utf-8') as f:
                        f.write(vtt_cue)
                    
                    ass_line = create_ass_subtitle(final_text, sub_start, sub_end)
                    with open(ass_path, 'a', encoding='utf-8') as f:
                        f.write(ass_line + '\n')
                    
                    log_msg = f"{final_text}"
                    if target_lang != 'original':
                        log_msg += f" ({text_original})"
                        
                    processing_status[task_id]['last_subtitle'] = log_msg
                    processing_status[task_id]['current_segment'] = seq
                    
                    subtitle_cache[task_id].append({
                        'start': sub_start, 'end': sub_end, 'text': final_text
                    })
                    
                    if not processing_status[task_id]['preview_ready'] and seq == 3:
                        preview = await generate_preview_with_burned_subtitles(task_id, source_url, ass_path)
                        if preview:
                            processing_status[task_id]['preview_ready'] = True
                            processing_status[task_id]['preview_file'] = preview

                try: os.remove(clip_file)
                except: pass
            
            seq += 1
            
            elapsed = time.time() - start_proc
            sleep_time = max(0.5, segment_duration - elapsed)
            await asyncio.sleep(sleep_time)

        except Exception as e:
            print(f"Worker Error: {e}")
            await asyncio.sleep(1)

    if task_id in hls_processes:
        try:
            hls_processes[task_id].terminate()
            del hls_processes[task_id]
        except: pass
    print(f"🛑 Worker stopped for {task_id}")

# ---------- API MODELS & ROUTES ----------
class StartRequest(BaseModel):
    source_url: str
    source_lang: str = "auto"
    target_lang: str = "id"

@app.get("/health")
async def health_check():
    """Health check endpoint untuk Cloud Service"""
    return {"status": "ok", "whisper_loaded": whisper_model is not None}

@app.post("/start")
async def start_streaming(req: StartRequest, background: BackgroundTasks):
    task_id = str(uuid.uuid4())
    active_tasks[task_id] = True
    background.add_task(realtime_subtitle_worker, task_id, req.source_url, req.source_lang, req.target_lang)
    return {"status": "started", "task_id": task_id}

@app.post("/stop/{task_id}")
async def stop_streaming(task_id: str):
    if task_id in active_tasks:
        del active_tasks[task_id]
    return {"status": "stopped"}

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id in processing_status:
        return processing_status[task_id]
    return {"status": "not_found"}

@app.get("/proxy_stream")
async def proxy_stream(url: str):
    try:
        resp = requests.get(url, verify=False, timeout=5, stream=True)
        return StreamingResponse(
            resp.iter_content(chunk_size=1024), 
            media_type=resp.headers.get('content-type', 'application/vnd.apple.mpegurl'),
            headers={"Access-Control-Allow-Origin": "*"}
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/", response_class=HTMLResponse)
async def index():
    html = """
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Subtitle & Translation</title>
    <link href="https://vjs.zencdn.net/7.20.3/video-js.css" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .vjs-text-track-cue { 
            background-color: rgba(0,0,0,0.7) !important; 
            color: #fbbf24 !important; 
            font-size: 1.4em !important;
        }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen p-4">
    <div class="max-w-7xl mx-auto">
        <div class="flex justify-between items-center mb-6 bg-gray-800 p-4 rounded-xl shadow">
            <h1 class="text-2xl font-bold text-blue-400">🌐 Live Stream Translator + Hardsub</h1>
            <div id="statusBadge" class="px-3 py-1 rounded text-xs font-bold bg-gray-700">IDLE</div>
        </div>

        <!-- Controls -->
        <div class="grid grid-cols-1 lg:grid-cols-4 gap-4 mb-6">
            <div class="lg:col-span-3 bg-gray-800 p-4 rounded-xl">
                <label class="text-xs font-bold text-gray-400">STREAM URL</label>
                <input type="text" id="streamUrl" class="w-full bg-gray-900 border border-gray-600 rounded p-2 mt-1" value="https://kangmas.transvision.co.id/Bioskop_Ind_POC_Sub/index.m3u8">
            </div>
            <div class="bg-gray-800 p-4 rounded-xl flex flex-col gap-2">
                <select id="targetLang" class="bg-gray-900 border border-gray-600 rounded p-2">
                    <option value="id">🇮🇩 Indonesia</option>
                    <option value="en">🇬🇧 English</option>
                    <option value="original">Original Text</option>
                </select>
                <button onclick="startEngine()" id="startBtn" class="bg-green-600 hover:bg-green-700 text-white font-bold py-2 rounded transition">START</button>
                <button onclick="stopEngine()" id="stopBtn" disabled class="bg-red-600 hover:bg-red-700 text-white font-bold py-2 rounded transition disabled:opacity-50">STOP</button>
            </div>
        </div>

        <!-- Video Grid -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            
            <!-- 1. Original Stream + Soft Subtitle -->
            <div class="bg-black rounded-xl overflow-hidden shadow-lg relative group">
                <div class="absolute top-2 left-2 z-10 bg-black/70 px-2 py-1 rounded text-xs text-white">Original + Soft Sub</div>
                <video-js id="playerSoft" class="vjs-default-skin vjs-big-play-centered" controls preload="auto" style="width:100%; height:300px;"></video-js>
            </div>

            <!-- 2. HLS Output with Burned-in Subtitle -->
            <div class="bg-black rounded-xl overflow-hidden shadow-lg relative group">
                <div class="absolute top-2 left-2 z-10 bg-red-600/90 px-2 py-1 rounded text-xs text-white animate-pulse">HLS Hardsub Output</div>
                <video-js id="playerHard" class="vjs-default-skin vjs-big-play-centered" controls preload="auto" style="width:100%; height:300px;"></video-js>
                <div id="hlsWaiting" class="absolute inset-0 flex items-center justify-center bg-black/80 text-gray-400 text-sm">
                    Waiting for HLS generation...
                </div>
            </div>

            <!-- 3. Preview Video (Clip) -->
            <div class="bg-gray-800 rounded-xl p-4">
                <h3 class="text-sm font-bold text-gray-400 mb-2">📹 Short Preview Clip</h3>
                <video id="playerPreview" controls class="w-full h-48 bg-black rounded" style="display:none"></video>
                <div id="previewPlaceholder" class="w-full h-48 bg-black/50 rounded flex items-center justify-center text-xs text-gray-500">
                    Preview will appear here after ~15s
                </div>
            </div>

            <!-- 4. Live Logs -->
            <div class="bg-gray-800 rounded-xl p-4 h-60 flex flex-col">
                <h3 class="text-sm font-bold text-gray-400 mb-2">📝 Transcript Log</h3>
                <div id="logContainer" class="flex-1 overflow-y-auto font-mono text-xs space-y-1 pr-1 bg-gray-900 p-2 rounded"></div>
            </div>
        </div>
    </div>

    <script src="https://vjs.zencdn.net/7.20.3/video.min.js"></script>
    <script>
        let playerSoft = videojs('playerSoft');
        let playerHard = videojs('playerHard');
        let taskId = null;
        let pollInterval = null;

        async function startEngine() {
            const url = document.getElementById('streamUrl').value;
            const lang = document.getElementById('targetLang').value;
            
            if(!url) return alert("URL Required");

            document.getElementById('startBtn').disabled = true;
            document.getElementById('stopBtn').disabled = false;
            document.getElementById('statusBadge').innerText = "INITIALIZING...";
            document.getElementById('statusBadge').className = "px-3 py-1 rounded text-xs font-bold bg-yellow-600";

            // 1. Start Backend
            const res = await fetch('/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ source_url: url, target_lang: lang })
            });
            const data = await res.json();
            taskId = data.task_id;

            // 2. Load Player Soft (Original + Proxy)
            const proxyUrl = `/proxy_stream?url=${encodeURIComponent(url)}`;
            playerSoft.src({ src: proxyUrl, type: 'application/x-mpegURL' });
            playerSoft.play().catch(()=>{});

            // 3. Setup VTT Track for Soft Player
            setupVTT(taskId);

            // 4. Start Polling Status
            pollInterval = setInterval(pollStatus, 2000);
        }

        function setupVTT(tid) {
            const old = playerSoft.remoteTextTracks();
            for(let i=0; i<old.length; i++) playerSoft.removeRemoteTextTrack(old[i]);
            
            playerSoft.addRemoteTextTrack({
                kind: 'captions',
                label: 'Live Translate',
                src: `/static/subtitles_${tid}.vtt`,
                default: true
            }, false);
            
            // Hack to refresh VTT
            setInterval(() => {
                // Logic refresh track (browser caching is tricky)
            }, 5000);
        }

        async function pollStatus() {
            const res = await fetch(`/status/${taskId}`);
            const status = await res.json();

            if(status.last_subtitle) {
                const logs = document.getElementById('logContainer');
                const div = document.createElement('div');
                div.className = "text-green-400 border-b border-gray-800 pb-1";
                div.innerText = status.last_subtitle;
                logs.appendChild(div);
                logs.scrollTop = logs.scrollHeight;
            }

            // Check Preview
            if(status.preview_ready && status.preview_file) {
                const pVideo = document.getElementById('playerPreview');
                if(pVideo.style.display === 'none') {
                    document.getElementById('previewPlaceholder').style.display = 'none';
                    pVideo.style.display = 'block';
                    pVideo.src = `/preview/${status.preview_file}`;
                }
            }

            // Check HLS Hardsub
            if(status.hls_ready && status.hls_url) {
                const waitDiv = document.getElementById('hlsWaiting');
                if(waitDiv.style.display !== 'none') {
                    waitDiv.style.display = 'none';
                    playerHard.src({ src: status.hls_url, type: 'application/x-mpegURL' });
                    playerHard.play().catch(()=>{});
                    document.getElementById('statusBadge').innerText = "STREAMING ACTIVE";
                    document.getElementById('statusBadge').className = "px-3 py-1 rounded text-xs font-bold bg-green-600";
                }
            }
        }

        async function stopEngine() {
            if(taskId) await fetch(`/stop/${taskId}`, {method: 'POST'});
            clearInterval(pollInterval);
            location.reload();
        }
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html)

if __name__ == "__main__":
    # Support PORT env var untuk cloud deployment
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)