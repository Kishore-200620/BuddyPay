
from fastapi import FastAPI

app = FastAPI(
    title="BuddyPay API",
    description="AI-powered invoice and payment platform",
    version="1.0.0"
)


@app.get("/")
def root():
    return {
        "message": "Welcome to BuddyPay API",
        "status": "running"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "application": "BuddyPay"
    }
