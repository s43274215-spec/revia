import json
import uuid
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
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint: str = ""
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket_name: str = ""
    s3_force_path_style: bool = False
    upload_url_expires_seconds: int = 900
    document_lease_seconds: int = 300
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://[::1]:3000",
    ]
    app_access_code: str = "revia-local"
    owner_access_code: str = ""
    owner_workspace_id: uuid.UUID | None = None
    demo_access_code: str = ""
    demo_workspace_id: uuid.UUID | None = None
    public_access_enabled: bool = False
    session_signing_key: str = "revia-local-session-signing-key-change-me"
    session_max_age_seconds: int = 60 * 60 * 24 * 30
    credential_encryption_key: str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
    ai_mode: Literal["mock", "live"] = "mock"
    ai_provider: str = "deepseek"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    ai_timeout_seconds: float = 60.0
    ai_max_output_tokens: int = 4096
    ai_temperature: float = 0.1
    generation_stale_seconds: int = 1200
    matching_threshold: float = 0.35
    matching_max_candidates: int = 6
    ocr_enabled: bool = True
    ocr_dpi: int = 144
    ocr_minimum_text_length: int = 8
    ocr_worker_max_rss_mb: int = 300
    ocr_worker_max_pages: int = 1
    ocr_container_memory_budget_mb: int = 480
    ocr_worker_threads: int = 1
    ocr_worker_timeout_seconds: int = 180
    github_ocr_token: str = ""
    github_ocr_repository: str = ""
    github_ocr_workflow: str = "revia-ocr.yml"
    github_ocr_ref: str = "main"
    github_ocr_worker_key: str = ""
    github_ocr_api_timeout_seconds: int = 30
    github_ocr_lease_seconds: int = 900
    github_ocr_download_url_expires_seconds: int = 900
    document_memory_diagnostics_enabled: bool = False
    max_upload_mb: int = 150
    max_pdf_pages: int = 600
    workspace_max_active_documents: int = 1
    workspace_rolling_24h_page_limit: int = 1200
    global_max_processing_documents: int = 1
    global_rolling_24h_page_limit: int = 3000

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
        if min(
            self.ocr_worker_max_rss_mb,
            self.ocr_worker_max_pages,
            self.ocr_container_memory_budget_mb,
            self.ocr_worker_threads,
            self.ocr_worker_timeout_seconds,
            self.github_ocr_api_timeout_seconds,
            self.github_ocr_lease_seconds,
            self.github_ocr_download_url_expires_seconds,
        ) <= 0:
            raise ValueError("OCR limits must be positive integers")
        github_values = {
            "GITHUB_OCR_TOKEN": self.github_ocr_token.strip(),
            "GITHUB_OCR_REPOSITORY": self.github_ocr_repository.strip(),
            "GITHUB_OCR_WORKER_KEY": self.github_ocr_worker_key.strip(),
        }
        configured_count = sum(bool(value) for value in github_values.values())
        if configured_count not in {0, len(github_values)}:
            missing = [name for name, value in github_values.items() if not value]
            raise ValueError(f"GitHub OCR configuration is incomplete: {', '.join(missing)}")
        if self.github_ocr_repository and self.github_ocr_repository.count("/") != 1:
            raise ValueError("GITHUB_OCR_REPOSITORY must use OWNER/REPO format")
        if not self.github_ocr_workflow.strip() or not self.github_ocr_ref.strip():
            raise ValueError("GITHUB_OCR_WORKFLOW and GITHUB_OCR_REF cannot be empty")
        if min(
            self.workspace_max_active_documents,
            self.workspace_rolling_24h_page_limit,
            self.global_max_processing_documents,
            self.global_rolling_24h_page_limit,
        ) <= 0:
            raise ValueError("Document quota limits must be positive integers")
        if min(
            self.upload_url_expires_seconds,
            self.document_lease_seconds,
            self.generation_stale_seconds,
            self.session_max_age_seconds,
        ) <= 0:
            raise ValueError("Upload URL, document lease, and generation stale durations must be positive")
        if bool(self.demo_access_code) != bool(self.demo_workspace_id):
            raise ValueError("DEMO_ACCESS_CODE and DEMO_WORKSPACE_ID must be configured together")
        if self.owner_workspace_id is not None and self.demo_workspace_id == self.owner_workspace_id:
            raise ValueError("OWNER_WORKSPACE_ID and DEMO_WORKSPACE_ID must be different")
        if self.environment.casefold() == "production":
            required = {
                "OWNER_ACCESS_CODE (or APP_ACCESS_CODE)": self.effective_owner_access_code,
                "OWNER_WORKSPACE_ID": self.owner_workspace_id,
                "DEMO_ACCESS_CODE": self.demo_access_code,
                "DEMO_WORKSPACE_ID": self.demo_workspace_id,
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
            if self.effective_owner_access_code == "revia-local" or self.session_signing_key.startswith("revia-local"):
                raise ValueError("Production access and signing secrets must be explicitly configured")
            if self.credential_encryption_key == "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=":
                raise ValueError("Production CREDENTIAL_ENCRYPTION_KEY must be explicitly configured")
            if self.storage_backend != "s3":
                raise ValueError("Production STORAGE_BACKEND must be s3")
            s3_required = {
                "S3_ENDPOINT": self.s3_endpoint,
                "S3_REGION": self.s3_region,
                "S3_ACCESS_KEY_ID": self.s3_access_key_id,
                "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
                "S3_BUCKET_NAME": self.s3_bucket_name,
            }
            s3_missing = [name for name, value in s3_required.items() if not value]
            if s3_missing:
                raise ValueError(f"Production S3 configuration is missing: {', '.join(s3_missing)}")
        return self

    @property
    def github_ocr_enabled(self) -> bool:
        return bool(
            self.github_ocr_token.strip()
            and self.github_ocr_repository.strip()
            and self.github_ocr_worker_key.strip()
        )

    @property
    def effective_owner_access_code(self) -> str:
        return self.owner_access_code or self.app_access_code


@lru_cache
def get_settings() -> Settings:
    return Settings()
