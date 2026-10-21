from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class DatabaseSettings(BaseSettings):
    """Only what is needed to reach this service's database.

    The migration job (alembic) imports only this class, so it runs with the
    database variables alone: no JWT key and no internal key.
    """

    # hide_input_in_errors: a configuration error names the wrong field but
    # never prints the received values, which include passwords.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "orders_db"
    db_user: str = "order_svc"
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


db_settings = DatabaseSettings()
