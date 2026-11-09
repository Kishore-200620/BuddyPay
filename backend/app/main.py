from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.routes import health, users, auth, invoices, paypal
app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "AI-powered autonomous service procurement "
        "and conditional payment platform."
    ),
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    health.router,
    prefix=settings.API_PREFIX,
)

app.include_router(
    users.router,
    prefix=settings.API_PREFIX,
)

app.include_router(
    auth.router,
    prefix=settings.API_PREFIX,
)

app.include_router(
    invoices.router,
    prefix=settings.API_PREFIX,
)

app.include_router(
    paypal.router,
    prefix=settings.API_PREFIX,
)
@app.get("/")
async def root():
    return {
        "application": settings.APP_NAME,
        "status": "running",
        "api_version": settings.APP_VERSION,
        "documentation": "/docs",
    }
