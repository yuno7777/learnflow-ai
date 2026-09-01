"""Runtime configuration module for LearnFlow AI.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file defines the central `Settings` class that loads, validates, and exposes
all environment variables and configuration options for the entire backend.
It uses Pydantic's `BaseSettings` so that environment variables (e.g. `DATABASE_URL`,
`JWT_SECRET`, `LLM_PROVIDER`) are automatically read from the OS environment or from
a `.env` file, converted to their proper Python types, and checked for validity.

===============================================================================
WHY THIS FILE EXISTS & WHAT HAPPENS IF REMOVED:
===============================================================================
If this file were removed:
1. Every component across the backend would have to read `os.environ` manually,
   leading to duplicated string parsing, missing type safety, and hard-to-find bugs.
2. The application would lose centralized defaults (e.g. default chunk sizes,
   fallback URLs, token expiration times, rate limits).
3. Secret management would become scattered and prone to leaks.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- Imported by almost every backend file via: `from ..config import settings`
- Key Consumers:
  * `db.py`: Reads `settings.database_url` to initialize the SQLAlchemy engine.
  * `redis_client.py`: Reads `settings.redis_url` to connect to Redis.
  * `security.py`: Reads `settings.jwt_secret`, `jwt_algorithm`, `access_token_ttl_minutes`.
  * `middleware.py`: Reads `settings.rate_limit_per_minute` and `settings.cors_origins`.
  * `ai/gateway.py` & `ai/registry.py`: Reads `settings.llm_provider`, `llm_model`, `openai_api_key`.
  * `rag/pipeline.py` & `rag/chunking.py`: Reads `chunk_size_tokens`, `chunk_overlap_tokens`.
  * `automation/engine.py` & `automation/n8n_client.py`: Reads `n8n_base_url`, `webhook_signing_secret`.
  * `worker/main.py`: Reads `redis_url` for ARQ background worker initialization.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration schema.

    Pydantic automatically inspects environment variables and maps matching
    variable names (case-insensitive) to these class attributes.
    """

    # Tell Pydantic to read from a local `.env` file if present, and ignore any extra variables.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Application Environment ---
    # Controls debug behavior, logging format (JSON in prod vs pretty console in dev),
    # and whether test mocks or real external connections are used.
    environment: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Datastores Configuration ---
    # 1. PostgreSQL connection string (uses asyncpg for non-blocking async DB operations).
    #    Consumed by: `learnflow/db.py` to create the SQLAlchemy async engine.
    database_url: str = "postgresql+asyncpg://learnflow:learnflow@localhost:5432/learnflow"

    # 2. Redis connection URL:
    #    Consumed by: `learnflow/redis_client.py`, `middleware.py` (rate limiting),
    #    and `worker/main.py` (ARQ job queue).
    #    If left empty, the system runs in single-node fallback mode (no live SSE pub/sub,
    #    and background jobs fall back to database polling in `worker/standalone.py`).
    redis_url: str = "redis://localhost:6379/0"

    # 3. Job Queue mode: "arq" (uses Redis) or "database" (polls `jobs` table via standalone worker).
    job_queue: str = "arq"

    # 4. ChromaDB Vector Store:
    #    Consumed by: `learnflow/rag/vectorstore.py` to connect to Chroma's HTTP server.
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    # When set to a folder path, Chroma runs in-memory/embedded on disk (used during automated tests/CLI).
    chroma_persist_dir: str | None = None

    # --- Authentication & Cryptography ---
    # Consumed by: `learnflow/security.py` and `learnflow/deps.py`.
    # Used to sign and verify HMAC-SHA256 JWT access tokens.
    jwt_secret: str = Field(default="dev-only-change-me", min_length=8)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30  # Short-lived access token for stateless API requests
    refresh_token_ttl_days: int = 14     # Long-lived single-use rotating refresh token
    password_min_length: int = 10

    # --- Webhooks & n8n Workflow Automation ---
    # Consumed by: `learnflow/automation/n8n_client.py`, `routers/webhooks.py`, and `security.py`.
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str | None = None
    # Secret key used to generate and verify HMAC-SHA256 signatures (`X-LearnFlow-Signature`)
    # on all outbound webhooks sent to n8n and inbound callbacks received from n8n.
    webhook_signing_secret: str = "dev-only-webhook-secret"
    webhook_tolerance_seconds: int = 300  # 5-minute freshness window to prevent replay attacks
    public_api_base_url: str = "http://localhost:8000"

    # --- AI & Large Language Model Providers ---
    # Consumed by: `learnflow/ai/gateway.py`, `ai/registry.py`, and provider adapters.
    # LLM_PROVIDER can be: "mock" | "openai" | "azure_openai" | "ollama" | "huggingface"
    llm_provider: str = "mock"
    # Secondary provider if primary fails after retries (e.g. fallback from OpenAI to local Ollama)
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

    # --- Ingestion & RAG Subsystem ---
    # Consumed by: `learnflow/rag/pipeline.py`, `rag/chunking.py`, `rag/retrieval.py`.
    upload_dir: str = "/data/uploads"          # Directory where raw uploaded PDFs/DOCX are stored
    max_upload_bytes: int = 25 * 1024 * 1024   # 25 MB max upload file size
    chunk_size_tokens: int = 700               # Target token budget per chunk
    chunk_overlap_tokens: int = 100            # Overlap tokens carried forward to preserve context
    retrieval_top_k: int = 6                   # Number of citations to include in final prompt
    retrieval_min_score: float = 0.25          # Minimum RRF score threshold before triggering refusal

    # --- Operations, Networking & Email ---
    # Consumed by: `learnflow/main.py` (CORS), `middleware.py` (Rate Limiting), `automation/email.py`.
    cors_origins: list[str] = ["http://localhost:3000"]
    rate_limit_per_minute: int = 120           # Fixed-window max requests per IP / API key
    smtp_host: str | None = None               # Unset in dev/tests => triggers MockEmailAdapter
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str = "no-reply@learnflow.local"


@lru_cache
def get_settings() -> Settings:
    """Creates and caches a single `Settings` instance.

    `@lru_cache` ensures we only parse environment variables and load the `.env` file once,
    avoiding disk I/O and overhead on every request.
    """
    return Settings()


# Global singleton instance ready for direct import throughout the app.
settings = get_settings()
