# Internal and Confidential - Not for External Distribution.
"""Provide an HTTP adapter for structured chat completion through Ollama."""

import json
import urllib.request

from app.config import get_settings


class OllamaAdapter:
    """Send deterministic JSON-format chat requests to a configured Ollama host."""

    def __init__(self) -> None:
        """Initialize the adapter from the current application settings."""

        settings = get_settings()
        self._host = settings.ollama_host.rstrip("/")
        self._model = settings.ollama_model

    def chat(self, prompt: str, *, response_format: str | dict = "json") -> str:
        """Request a structured completion from Ollama.

        Args:
            prompt: User-message content sent to the configured model.
            response_format: Ollama JSON mode. ``"json"`` for free-form JSON.
                A schema dict constrains the keys, which the task generator needs.

        Returns:
            The raw content from Ollama's assistant message.
        """
        options: dict = {"temperature": 0, "seed": 77}
        if isinstance(response_format, dict):
            # The default 4096-token window drops the task-list instructions.
            options["num_ctx"] = 8192
        body = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "format": response_format,
                "stream": False,
                "think": False,
                "options": options,
            }
        ).encode()
        request = urllib.request.Request(
            f"{self._host}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read())
        return payload["message"]["content"]
