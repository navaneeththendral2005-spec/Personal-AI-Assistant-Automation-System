from fastapi import FastAPI

app = FastAPI(
    title="FRIDAY Personal Assistant",
    description="A secure, voice-enabled personal AI assistant.",
    version="0.1.0",
)


@app.get("/")
def home():
    return {
        "assistant": "FRIDAY",
        "status": "online",
        "message": "Hello! FRIDAY is ready.",
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "friday-backend",
    }