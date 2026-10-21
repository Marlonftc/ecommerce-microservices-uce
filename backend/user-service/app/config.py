from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Service configuration, read from environment variables.

    When running locally, values are also read from a ".env" file in the
    service folder. Inside Docker, Compose provides them.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "user-service"
    log_level: str = "INFO"

    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "users_db"
    db_user: str = "user_svc"
    db_password: str

    @property
    def database_url(self) -> URL:
        # URL.create escapes special characters in the password
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


settings = Settings()
