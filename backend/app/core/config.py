import json
from functools import lru_cache
from typing import Annotated, Literal

from cryptography.fernet import Fernet
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Revia API"
    api_v1_prefix: str = "/api/v1"
    environment: str = "development"
    database_url: str = "sqlite+pysqlite:///./storage/revia.db"
    file_storage_root: str = "./storage"
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://[::1]:3000",
    ]
    app_access_code: str = "revia-local"
    session_signing_key: str = "revia-local-session-signing-key-change-me"
    credential_encryption_key: str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    ai_mode: Literal["mock", "live"] = "mock"
    ai_provider: str = "deepseek"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    ai_timeout_seconds: float = 60.0
    ai_max_output_tokens: int = 4096
    ai_temperature: float = 0.1
    matching_threshold: float = 0.35
    matching_max_candidates: int = 3
    ocr_enabled: bool = True
    ocr_dpi: int = 144
    ocr_minimum_text_length: int = 8
    max_upload_mb: int = 25
    max_pdf_pages: int = 120

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if normalized.startswith("postgres://"):
            return "postgresql+psycopg://" + normalized.removeprefix("postgres://")
        if normalized.startswith("postgresql://"):
            return "postgresql+psycopg://" + normalized.removeprefix("postgresql://")
        return normalized

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, list):
            return value
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if not isinstance(parsed, list):
                raise ValueError("CORS_ORIGINS JSON must be an array")
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in stripped.split(",") if item.strip()]

    @model_validator(mode="after")
    def validate_runtime_configuration(self) -> "Settings":
        if len(self.session_signing_key.encode("utf-8")) < 32:
            raise ValueError("SESSION_SIGNING_KEY must contain at least 32 bytes")
        try:
            Fernet(self.credential_encryption_key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise ValueError("CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from exc
        if self.max_upload_mb <= 0 or self.max_pdf_pages <= 0:
            raise ValueError("Upload limits must be positive integers")
        if self.environment.casefold() == "production":
            required = {
                "APP_ACCESS_CODE": self.app_access_code,
                "SESSION_SIGNING_KEY": self.session_signing_key,
                "CREDENTIAL_ENCRYPTION_KEY": self.credential_encryption_key,
                "DATABASE_URL": self.database_url,
                "CORS_ORIGINS": self.cors_origins,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"Production configuration is missing: {', '.join(missing)}")
            if self.database_url.startswith("sqlite") or "localhost" in self.database_url or "127.0.0.1" in self.database_url:
                raise ValueError("Production DATABASE_URL must point to PostgreSQL, not a local database")
            if any("localhost" in origin or "127.0.0.1" in origin or "[::1]" in origin for origin in self.cors_origins):
                raise ValueError("Production CORS_ORIGINS must not contain local development origins")
            if self.ai_mode != "live":
                raise ValueError("Production AI_MODE must be live")
            if self.app_access_code == "revia-local" or self.session_signing_key.startswith("revia-local"):
                raise ValueError("Production access and signing secrets must be explicitly configured")
            if self.credential_encryption_key == "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=":
                raise ValueError("Production CREDENTIAL_ENCRYPTION_KEY must be explicitly configured")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
