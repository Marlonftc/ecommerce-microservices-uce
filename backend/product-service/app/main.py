import logging
import socket

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import check_database
from app.routers import products

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(settings.service_name)

# Container hostname: different for each replica (used in the scaling tests)
INSTANCE_ID = socket.gethostname()

app = FastAPI(title=settings.service_name, version="1.0.0")
app.include_router(products.router)


@app.middleware("http")
async def add_instance_header(request: Request, call_next):
    """Adds the replica that answered the request to every response."""
    response = await call_next(request)
    response.headers["X-Instance"] = INSTANCE_ID
    return response


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    """The process is running."""
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready", tags=["health"])
def health_ready() -> JSONResponse:
    """The service can work: the database is reachable."""
    try:
        check_database()
    except Exception as exc:
        logger.warning("database not reachable: %s", exc)
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
    return JSONResponse(status_code=200, content={"status": "ok", "database": "up"})
