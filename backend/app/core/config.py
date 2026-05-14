from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "BondRadar"
    API_PREFIX: str = "/api"
    DATABASE_URL: str = (
        "postgresql+psycopg://bondradar:bondradar@localhost:5432/bondradar"
    )
    BACKEND_CORS_ORIGINS: list[str] = []
    MOEX_ISS_BASE_URL: str = "https://iss.moex.com"
    MOEX_ISS_TIMEOUT_SECONDS: int = 20

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
