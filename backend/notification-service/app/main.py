import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import check_database
from app.routers import internal, notifications

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(settings.service_name)

app = FastAPI(title=settings.service_name, version="1.0.0")
app.include_router(notifications.router)
app.include_router(internal.router)


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    """The process is running."""
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready", tags=["health"])
def health_ready() -> JSONResponse:
    """The service can work: its own database is reachable."""
    try:
        check_database()
    except Exception as exc:
        logger.warning("database not reachable: %s", exc)
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
    return JSONResponse(status_code=200, content={"status": "ok", "database": "up"})
