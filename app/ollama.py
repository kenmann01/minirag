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

    def chat(self, prompt: str) -> str:
        """Request a structured completion from Ollama.

        Args:
            prompt: User-message content sent to the configured model.

        Returns:
            The raw content from Ollama's assistant message.
        """
        body = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "seed": 77},
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
