# Internal and Confidential - Not for External Distribution.
"""Load Mini RAG configuration from environment variables."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Environment-backed configuration for storage, embeddings, and models."""

    # The .env file is anchored at the repository root so running the CLI
    # from another working directory still finds the configuration.
    model_config = SettingsConfigDict(env_file=_REPO_ROOT / ".env", extra="ignore")

    database_url: str
    embedding_model: str = "Alibaba-NLP/gte-modernbert-base"
    ollama_model: str = "qwen3:8b"
    ollama_host: str = "http://host.docker.internal:11434"
    corpus_dir: str = "corpora/banking"
    input_usd_per_million: float = 0.15
    output_usd_per_million: float = 0.60
    tool_usd: float = 0.001
    judge_provider: Literal["ollama", "bedrock"] = "ollama"
    judge_model: str = ""
    aws_region: str = "us-east-1"


def get_settings() -> Settings:
    """Load and validate the current application settings.

    Returns:
        Settings populated from environment variables and the local ``.env``
        file.
    """
    return Settings()
