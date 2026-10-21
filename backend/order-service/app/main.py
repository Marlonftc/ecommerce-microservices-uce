import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import check_database

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(settings.service_name)

app = FastAPI(title=settings.service_name, version="1.0.0")


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    """The process is running."""
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready", tags=["health"])
def health_ready() -> JSONResponse:
    """The service can work: its own database is reachable.

    The other services are not checked here: if one of them is down,
    order-service keeps running and retries later.
    """
    try:
        check_database()
    except Exception as exc:
        logger.warning("database not reachable: %s", exc)
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
    return JSONResponse(status_code=200, content={"status": "ok", "database": "up"})
