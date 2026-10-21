from app.db_config import DatabaseSettings


class Settings(DatabaseSettings):
    """Configuration of the API: the database settings plus security.

    When running locally, values are also read from a ".env" file in the
    service folder. Inside Docker, Compose provides them.
    """

    service_name: str = "notification-service"
    log_level: str = "INFO"

    # JWT verification only: this service has the public key, not the private one
    jwt_public_key_path: str = "/run/secrets/jwt_public_key"
    jwt_algorithm: str = "RS256"
    jwt_issuer: str = "user-service"
    jwt_audience: str = "ecommerce-uce"

    # Shared key required by /internal endpoints (only order-service creates notifications)
    internal_api_key: str


settings = Settings()
