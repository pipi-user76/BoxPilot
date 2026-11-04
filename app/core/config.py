from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "BoxPilot API"
    PROJECT_VERSION: str = "0.1.0"
    SANDBOX_URL: str = "http://127.0.0.1:8080"
    SANDBOX_WORK_ROOT: str = "/home/gem/boxpilot"
    SANDBOX_TIMEOUT_SECONDS: int = 120
    SANDBOX_HEALTH_TIMEOUT_SECONDS: int = 5
    ARTIFACT_ROOT: Path = Path("data/artifacts")
    MAX_ARTIFACT_BYTES: int = 20 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
