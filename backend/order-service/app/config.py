import tempfile
from pathlib import Path
from typing import Self

from pydantic import model_validator

from app.db_config import DatabaseSettings


class Settings(DatabaseSettings):
    """Configuration of the API and the worker: the database settings plus
    security, the other services and the recovery timings.

    When running locally, values are also read from a ".env" file in the
    service folder. Inside Docker, Compose provides them.
    """

    service_name: str = "order-service"
    log_level: str = "INFO"

    # JWT verification only: this service has the public key, not the private one
    jwt_public_key_path: str = "/run/secrets/jwt_public_key"
    jwt_algorithm: str = "RS256"
    jwt_issuer: str = "user-service"
    jwt_audience: str = "ecommerce-uce"

    # Sent in X-Internal-Key on every call to an /internal endpoint
    internal_api_key: str

    # Other services, reached by their Compose service name
    product_service_url: str = "http://product-service:8000"
    inventory_service_url: str = "http://inventory-service:8000"
    payment_service_url: str = "http://payment-service:8000"
    notification_service_url: str = "http://notification-service:8000"
    http_connect_timeout_seconds: float = 2
    http_read_timeout_seconds: float = 5

    # Orders: a reserved order waits this long for its payment
    order_reservation_ttl_minutes: int = 15
    # An order still RESERVING after this time is released and cancelled
    reserving_sweep_minutes: int = 2

    # Worker (pending operations)
    worker_poll_seconds: float = 5
    worker_max_fast_attempts: int = 10
    worker_backoff_max_seconds: int = 300
    worker_slow_retry_seconds: int = 900
    # A claimed operation not finished in this time can be taken by another worker
    worker_lease_seconds: int = 60
    # The worker touches this file while it makes progress; its healthcheck reads it
    worker_heartbeat_file: str = str(Path(tempfile.gettempdir()) / "order-worker.heartbeat")
    worker_heartbeat_max_age_seconds: int = 60

    @model_validator(mode="after")
    def lease_covers_a_call(self) -> Self:
        # Longest call: a failed connection, one retry, then the full read timeout.
        # The lease must last at least twice that, so it never expires mid-call.
        longest_call = 2 * self.http_connect_timeout_seconds + self.http_read_timeout_seconds
        if self.worker_lease_seconds < 2 * longest_call:
            raise ValueError(f"WORKER_LEASE_SECONDS must be at least {2 * longest_call:g} "
                             f"with the current HTTP timeouts")
        # Between two heartbeats there is at most one pause and one call: a worker
        # busy with a slow call is never reported unhealthy
        if self.worker_heartbeat_max_age_seconds < self.worker_poll_seconds + 2 * longest_call:
            raise ValueError("WORKER_HEARTBEAT_MAX_AGE_SECONDS is too short for the poll interval "
                             "and the HTTP timeouts")
        return self


settings = Settings()
