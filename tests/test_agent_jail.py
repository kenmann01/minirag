"""Repository tools stay inside the repo root."""

import sys
import types
from types import SimpleNamespace

from app.config import Settings
from app.harness.agent import (
    TOOL_TURN_CAP,
    RunOutput,
    build_agent,
    list_directory,
    read_text,
    resolve_inside,
    run_agent,
)


def test_reads_stay_inside_the_repo_and_listings_are_sorted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "b.txt").write_text("bee", encoding="utf-8")
    (repo / "a.txt").write_text("aye", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")

    assert list_directory(repo, ".") == "a.txt\nb.txt"
    assert read_text(repo, "a.txt") == "aye"
    try:
        resolve_inside(repo, "../secret.txt")
    except PermissionError:
        return
    raise AssertionError("path escape was allowed")


def test_listing_a_file_and_reading_a_directory_report_clear_errors(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("aye", encoding="utf-8")
    assert list_directory(repo, "a.txt") == "not a directory: a.txt"
    assert read_text(repo, ".") == "not a file: ."


def _install_fake_pydantic_ai(monkeypatch):
    """Replace pydantic_ai with recording fakes so the agent builds without a host."""
    captured = {}

    class FakeAgent:
        def __init__(self, model, instructions=None, output_type=str):
            captured["model"] = model
            captured["instructions"] = instructions
            captured["output_type"] = output_type
            self.tools = {}

        def tool_plain(self, func=None):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            if func is not None:
                return register(func)
            return register

    fake = types.ModuleType("pydantic_ai")
    fake.Agent = FakeAgent
    fake.UsageLimits = lambda **kwargs: kwargs

    exceptions = types.ModuleType("pydantic_ai.exceptions")

    class UsageLimitExceeded(Exception):
        pass

    exceptions.UsageLimitExceeded = UsageLimitExceeded

    models_ollama = types.ModuleType("pydantic_ai.models.ollama")

    class FakeOllamaModel:
        def __init__(self, name, provider=None, settings=None):
            captured["model_name"] = name
            captured["provider"] = provider
            captured["model_settings"] = settings

    models_ollama.OllamaModel = FakeOllamaModel

    providers_ollama = types.ModuleType("pydantic_ai.providers.ollama")

    class FakeOllamaProvider:
        def __init__(self, base_url=None):
            captured["base_url"] = base_url

    providers_ollama.OllamaProvider = FakeOllamaProvider

    monkeypatch.setitem(sys.modules, "pydantic_ai", fake)
    monkeypatch.setitem(sys.modules, "pydantic_ai.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "pydantic_ai.models.ollama", models_ollama)
    monkeypatch.setitem(sys.modules, "pydantic_ai.providers.ollama", providers_ollama)
    return captured, UsageLimitExceeded


def _settings() -> Settings:
    return Settings(
        database_url="postgresql://minirag:minirag@127.0.0.1:5432/minirag",
        ollama_model="qwen3:8b",
        ollama_host="http://localhost:11434",
    )


def test_build_agent_registers_jailed_tools_and_reports_each_call(tmp_path, monkeypatch):
    captured, _ = _install_fake_pydantic_ai(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "note.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    events = []

    agent = build_agent(repo, _settings(), on_tool=events.append)
    tools = agent.tools

    assert captured["model_name"] == "qwen3:8b"
    assert captured["base_url"] == "http://localhost:11434/v1"
    assert captured["model_settings"] == {"temperature": 0, "seed": 77, "thinking": False}
    assert "repository root" in captured["instructions"]

    assert tools["list_dir"](".") == "note.txt"
    assert tools["read_file"]("note.txt") == "hello"
    assert tools["list_dir"]("../outside.txt") == "path is outside the repository"
    assert tools["read_file"]("../outside.txt") == "path is outside the repository"
    assert tools["read_file"]("missing.txt") == "not a file: missing.txt"
    assert tools["list_dir"]("missing.txt") == "not a directory: missing.txt"
    assert [event["title"] for event in events] == [
        "list_dir",
        "read_file",
        "list_dir",
        "read_file",
        "read_file",
        "list_dir",
    ]
    assert all(event["returned"] for event in events)


def test_build_agent_works_without_an_event_listener(tmp_path, monkeypatch):
    _install_fake_pydantic_ai(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "note.txt").write_text("hello", encoding="utf-8")

    agent = build_agent(repo, _settings())
    assert agent.tools["read_file"]("note.txt") == "hello"


def test_run_agent_reports_usage_from_the_result(monkeypatch):
    captured, _ = _install_fake_pydantic_ai(monkeypatch)
    result = SimpleNamespace(
        output="It calls approve in Loan.java.",
        usage=SimpleNamespace(input_tokens=11, output_tokens=7, tool_calls=2),
    )

    class Serving:
        def run_sync(self, prompt, usage_limits=None):
            captured["prompt"] = prompt
            captured["usage_limits"] = usage_limits
            return result

    output = run_agent(Serving(), "What does Loan call?")
    assert output == RunOutput(
        answer="It calls approve in Loan.java.", tool_calls=2, prompt_tokens=11, completion_tokens=7
    )
    assert captured["usage_limits"] == {
        "tool_calls_limit": TOOL_TURN_CAP,
        "request_limit": TOOL_TURN_CAP + 1,
    }


def test_run_agent_fails_closed_past_the_tool_cap(monkeypatch):
    _install_fake_pydantic_ai(monkeypatch)
    raised = sys.modules["pydantic_ai.exceptions"].UsageLimitExceeded

    class OverBudget:
        def run_sync(self, prompt, usage_limits=None):
            raise raised

    output = run_agent(OverBudget(), "What does Loan call?")
    assert output.answer == ""
    assert output.tool_calls == TOOL_TURN_CAP
    assert output.prompt_tokens == 0 and output.completion_tokens == 0
