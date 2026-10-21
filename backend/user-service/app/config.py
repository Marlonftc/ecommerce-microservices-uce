from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Service configuration, read from environment variables."""

    service_name: str = "user-service"
    log_level: str = "INFO"


settings = Settings()
