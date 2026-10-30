from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/")
async def root():
    return {
        "message": "Welcome to BuddyPay - PactFlow AI",
        "status": "running"
    }


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "application": "PactFlow AI",
        "version": "1.0.0"
    }