from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Service configuration, read from environment variables.

    When running locally, values are also read from a ".env" file in the
    service folder. Inside Docker, Compose provides them.
    """

    # hide_input_in_errors: a configuration error names the wrong field but
    # never prints the received values, which include passwords.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    service_name: str = "user-service"
    log_level: str = "INFO"

    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "users_db"
    db_user: str = "user_svc"
    db_password: str

    # JWT (RS256). Inside Docker the keys are mounted as Compose secrets.
    jwt_private_key_path: str = "/run/secrets/jwt_private_key"
    jwt_public_key_path: str = "/run/secrets/jwt_public_key"
    jwt_algorithm: str = "RS256"
    jwt_issuer: str = "user-service"
    jwt_audience: str = "ecommerce-uce"
    jwt_expire_minutes: int = 30

    # Initial administrator, created by app/seed.py
    admin_email: str | None = None
    admin_password: str | None = None
    admin_name: str = "Administrador"

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
