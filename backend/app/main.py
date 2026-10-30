from fastapi import FastAPI
from app.api.routes import users
from app.api.routes import health, users
from app.core.config import settings
from app.api.routes import auth
from app.api.routes import health


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "AI-powered autonomous service procurement "
        "and conditional payment platform."
    ),
    version=settings.APP_VERSION,
    debug=settings.DEBUG
)



app.include_router(
    health.router,
    prefix=settings.API_PREFIX
)
app.include_router(
    users.router,
    prefix=settings.API_PREFIX
)
app.include_router(
    auth.router,
    prefix=settings.API_PREFIX
)


@app.get("/")
async def root():
    return {
        "application": settings.APP_NAME,
        "status": "running",
        "api_version": settings.APP_VERSION,
        "documentation": "/docs"
    }