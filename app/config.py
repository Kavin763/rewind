"""Settings, read from environment variables (and a .env file if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # Hindsight (self-hosted via docker-compose.yml)
    hindsight_url: str = os.getenv("HINDSIGHT_URL", "http://localhost:8888")
    hindsight_api_key: str | None = os.getenv("HINDSIGHT_API_KEY") or None
    bank_id: str = os.getenv("HINDSIGHT_BANK_ID", "rewind-oncall")

    # LLM (Groq exposes an OpenAI-compatible API)
    llm_api_key: str = os.getenv("GROQ_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    llm_model: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "qwen/qwen3-32b")

    # How much memory context to pull into each triage
    recall_max_tokens: int = int(os.getenv("RECALL_MAX_TOKENS", "2048"))
    recall_budget: str = os.getenv("RECALL_BUDGET", "mid")  # low | mid | high
    # Queue retains in Hindsight's background worker (survives LLM rate limits on free tiers)
    retain_async: bool = os.getenv("RETAIN_ASYNC", "true").lower() in {"1", "true", "yes"}

    # Local incident log (the agent's working list; Hindsight is the long-term memory)
    incidents_file: Path = Path(os.getenv("INCIDENTS_FILE", str(ROOT / "data" / "incidents.json")))


settings = Settings()
