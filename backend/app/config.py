from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Zeabur auto-injects POSTGRES_CONNECTION_STRING via service reference
    # DATABASE_URL is set in service env vars as ${POSTGRES_CONNECTION_STRING}
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"

    # CORS — allow GitHub Pages frontend
    cors_origins: str = "https://retailbox-automation.github.io,http://localhost:8000,http://localhost:5500"

    # API config
    api_title: str = "Fast Track Platform API"
    api_version: str = "0.1.0"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
