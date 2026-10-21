from fastapi import FastAPI

from app.config import settings

app = FastAPI(title=settings.service_name, version="1.0.0")


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    """The process is running."""
    return {"status": "ok", "service": settings.service_name}
