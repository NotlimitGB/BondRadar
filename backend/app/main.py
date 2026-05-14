from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import settings


app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "BondRadar exposes issuer and bond data with informational analysis "
        "signals only. It does not provide buy or sell recommendations."
    ),
    version="0.1.0",
)

if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.PROJECT_NAME,
        "stage": "backend-foundation",
        "disclaimer": "Informational analysis signals only; no buy/sell advice.",
    }

