"""The Ollama adapter posts structured chat requests to the configured host."""

import json
import urllib.request

from app.generation.ollama import OllamaAdapter


class FakeResponse:
    """Stand-in for the urlopen context manager."""

    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_urlopen(monkeypatch, reply: str):
    captured = {}

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse({"message": {"content": reply}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def test_the_adapter_posts_a_json_mode_chat_request(monkeypatch):
    captured = _capture_urlopen(monkeypatch, '{"answer": "yes"}')
    adapter = OllamaAdapter()

    assert adapter.chat("Say hi", response_format="json") == '{"answer": "yes"}'
    assert captured["url"].endswith("/api/chat")
    body = captured["body"]
    assert body["messages"] == [{"role": "user", "content": "Say hi"}]
    assert body["format"] == "json"
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"] == {"temperature": 0, "seed": 77}


def test_a_schema_response_format_widens_the_context_window(monkeypatch):
    captured = _capture_urlopen(monkeypatch, '{"tier2": []}')
    schema = {"type": "object", "properties": {"tier2": {}}}
    adapter = OllamaAdapter()

    assert adapter.chat("Phrase tasks", response_format=schema) == '{"tier2": []}'
    body = captured["body"]
    assert body["format"] == schema
    # The default 4096-token window drops the task-list instructions.
    assert body["options"]["num_ctx"] == 8192
    assert body["options"]["temperature"] == 0
