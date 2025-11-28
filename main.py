from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "API is running successfully!", "status": "active"}

@app.get("/health")
async def health():
    return {"status": "healthy"}
