from fastapi import FastAPI
import os
import uvicorn

app = FastAPI(title="Subtitle Translator API")

@app.get("/")
async def root():
    return {
        "message": "API is running successfully!",
        "status": "active",
        "port": os.getenv("PORT", "8000")
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "subtitle-translator"}

# Penting: Handle port secara explicit
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
