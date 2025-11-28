from fastapi import FastAPI
import os
import socket

app = FastAPI(title="Subtitle Translator API")

@app.get("/")
async def root():
    return {
        "message": "Subtitle Translator API is running!",
        "status": "success", 
        "hostname": socket.gethostname(),
        "port": os.getenv("PORT", "8000")
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "subtitle-translator"}

@app.get("/test")
async def test():
    return {"test": "success", "data": "API is working perfectly!"}

@app.get("/env")
async def show_env():
    return {
        "port": os.getenv("PORT"),
        "python_version": os.getenv("PYTHON_VERSION"),
        "railway_environment": os.getenv("RAILWAY_ENVIRONMENT")
    }

# Penting untuk Railway
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, access_log=True)
