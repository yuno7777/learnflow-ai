"""Runtime configuration. Everything comes from the environment; no secrets in code."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- datastores ---
    database_url: str = "postgresql+asyncpg://learnflow:learnflow@localhost:5432/learnflow"
    redis_url: str = "redis://localhost:6379/0"
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    # When set, Chroma runs embedded on disk instead of over HTTP (used by tests/CLI).
    chroma_persist_dir: str | None = None

    # --- auth ---
    jwt_secret: str = Field(default="dev-only-change-me", min_length=8)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14
    password_min_length: int = 10

    # --- webhooks / n8n ---
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str | None = None
    webhook_signing_secret: str = "dev-only-webhook-secret"
    webhook_tolerance_seconds: int = 300
    public_api_base_url: str = "http://localhost:8000"

    # --- AI ---
    llm_provider: str = "mock"  # mock | openai | azure_openai | ollama | huggingface
    llm_fallback_provider: str | None = None
    llm_model: str = "gpt-4o-mini"
    embedding_provider: str = "mock"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    azure_openai_endpoint: str | None = None
    azure_openai_api_version: str = "2024-08-01-preview"
    ollama_base_url: str = "http://localhost:11434"
    huggingface_base_url: str | None = None
    huggingface_api_key: str | None = None
    llm_timeout_seconds: int = 90
    llm_max_retries: int = 3

    # --- ingestion ---
    upload_dir: str = "/data/uploads"
    max_upload_bytes: int = 25 * 1024 * 1024
    chunk_size_tokens: int = 700
    chunk_overlap_tokens: int = 100
    retrieval_top_k: int = 6
    retrieval_min_score: float = 0.25

    # --- ops ---
    cors_origins: list[str] = ["http://localhost:3000"]
    rate_limit_per_minute: int = 120
    smtp_host: str | None = None  # unset => mock email adapter
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str = "no-reply@learnflow.local"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
