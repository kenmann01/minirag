# Internal and Confidential - Not for External Distribution.
"""Provide an HTTP adapter for structured chat completion through Ollama."""

import json
import urllib.request

from app.config import get_settings


class ChatReply(str):
    """Model content that also carries the provider's token counts.

    A str subclass keeps every existing caller that parses the reply as text
    working unchanged, while the candidate can forward accurate usage.
    """

    def __new__(
        cls,
        content: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model: str = "",
    ) -> "ChatReply":
        reply = super().__new__(cls, content)
        reply.prompt_tokens = int(prompt_tokens or 0)
        reply.completion_tokens = int(completion_tokens or 0)
        reply.model = model
        return reply


class OllamaAdapter:
    """Send deterministic JSON-format chat requests to a configured Ollama host."""

    def __init__(self) -> None:
        """Initialize the adapter from the current application settings."""

        settings = get_settings()
        self._host = settings.ollama_host.rstrip("/")
        self._model = settings.ollama_model
        self.model_name = settings.ollama_model

    def chat(
        self,
        prompt: str,
        *,
        response_format: str | dict = "json",
        seed: int = 77,
    ) -> ChatReply:
        """Request a structured completion from Ollama.

        Args:
            prompt: User-message content sent to the configured model.
            response_format: Ollama JSON mode. The string "json" asks for
                free-form JSON. A schema dict constrains the keys, which the
                task generator needs.
            seed: Sampling seed. The documented default stays 77 so a plain
                python -m app score run behaves exactly as before.

        Returns:
            The assistant content as a ChatReply carrying prompt and
            completion token counts from prompt_eval_count and eval_count.
        """
        options: dict = {"temperature": 0, "seed": 77 if seed is None else seed}
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
        return ChatReply(
            payload["message"]["content"],
            prompt_tokens=int(payload.get("prompt_eval_count") or 0),
            completion_tokens=int(payload.get("eval_count") or 0),
            model=str(payload.get("model") or self._model),
        )
