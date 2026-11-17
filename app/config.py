"""Settings from environment variables (or a .env file)."""

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    # Model: any OpenAI-compatible API. Default is a free local Ollama server.
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "ollama")
    llm_model: str = os.getenv("LLM_MODEL", "llama3.1")
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "120"))

    # Storage: SQLite file by default, or postgresql://user:pass@host/db
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/extractions.db")

    # Review: fields below this confidence are flagged for a person to check.
    review_threshold: float = float(os.getenv("REVIEW_THRESHOLD", "0.7"))

    # Upload limits (keep model costs and abuse under control).
    max_upload_mb: float = float(os.getenv("MAX_UPLOAD_MB", "5"))
    max_pages: int = int(os.getenv("MAX_PAGES", "5"))
    max_text_chars: int = int(os.getenv("MAX_TEXT_CHARS", "15000"))

    # Public demo protection.
    rate_limit_per_hour: int = int(os.getenv("RATE_LIMIT_PER_HOUR", "20"))
    demo_access_code: str = os.getenv("DEMO_ACCESS_CODE", "")  # empty = no code needed


settings = Settings()
