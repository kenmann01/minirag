# Internal and Confidential — Not for External Distribution.
"""Load Mini RAG configuration from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration for storage, embeddings, and Ollama."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    embedding_model: str = "all-MiniLM-L6-v2"
    ollama_model: str = "qwen3:8b"
    ollama_host: str = "http://host.docker.internal:11434"


def get_settings() -> Settings:
    """Load and validate the current application settings.

    Returns:
        Settings populated from environment variables and the local ``.env``
        file.
    """
    return Settings()
