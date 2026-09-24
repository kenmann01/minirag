import json
import urllib.request

from app.config import get_settings


class OllamaAdapter:
    def __init__(self) -> None:
        settings = get_settings()
        self._host = settings.ollama_host.rstrip("/")
        self._model = settings.ollama_model

    def chat(self, prompt: str) -> str:
        body = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "seed": 42},
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
