"""Judge adapters: dispatch, Bedrock converse wiring, and verdict parsing."""

import pytest

from app.config import Settings
from app.generation.judge import (
    BedrockJudgeAdapter,
    OllamaJudgeAdapter,
    judge_adapter,
    parse_verdict_text,
    reference_body,
)


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql://minirag:minirag@127.0.0.1:5432/minirag",
        "ollama_model": "qwen3:8b",
    }
    values.update(overrides)
    return Settings(**values)


class FakeBedrock:
    """Records the converse call and returns a fixed reply."""

    def __init__(self, reply: str, usage: dict | None = None):
        self.reply = reply
        self.usage = usage or {"inputTokens": 210, "outputTokens": 12}
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "output": {"message": {"content": [{"text": self.reply}]}},
            "usage": self.usage,
        }


def test_the_default_judge_is_the_local_ollama_adapter():
    assert isinstance(judge_adapter(_settings()), OllamaJudgeAdapter)


def test_an_unknown_judge_provider_is_rejected():
    with pytest.raises(Exception):
        judge_adapter(_settings(judge_provider="vibes"))


def test_bedrock_requires_an_explicit_model():
    adapter = BedrockJudgeAdapter(_settings(judge_provider="bedrock"), client=FakeBedrock("{}"))
    with pytest.raises(RuntimeError, match="JUDGE_MODEL"):
        adapter.verdict("instructions", "body")


def test_bedrock_sends_instructions_as_system_and_body_as_the_user_turn():
    fake = FakeBedrock('{"passed": true, "citation": "reg-z-1026:s1:c01"}')
    adapter = BedrockJudgeAdapter(
        _settings(judge_provider="bedrock", judge_model="anthropic.test:0"),
        client=fake,
    )
    verdict = adapter.verdict("cite exactly one chunk", "Question/Answer/References")
    assert verdict == {
        "passed": True,
        "citation": "reg-z-1026:s1:c01",
        "prompt_tokens": 210,
        "completion_tokens": 12,
    }
    (call,) = fake.calls
    assert call["modelId"] == "anthropic.test:0"
    assert call["system"] == [{"text": "cite exactly one chunk"}]
    assert call["messages"] == [
        {"role": "user", "content": [{"text": "Question/Answer/References"}]}
    ]
    assert call["inferenceConfig"]["temperature"] == 0.0


def test_bedrock_parses_fenced_and_prose_wrapped_replies():
    fake = FakeBedrock('Here is the verdict:\n```json\n{"passed": false, "citation": "x"}\n```')
    adapter = BedrockJudgeAdapter(_settings(judge_provider="bedrock", judge_model="m"), client=fake)
    assert adapter.verdict("i", "b")["passed"] is False


def test_a_non_json_judge_reply_raises_a_clear_error():
    with pytest.raises(ValueError, match="not JSON"):
        parse_verdict_text("the answer looks fine")


def test_the_reference_body_carries_question_answer_and_chunk_text():
    body = reference_body(
        "What rule does Loan state?",
        "The rule is about broker fees.",
        [{"chunk_id": "reg-z-1026:s1:c01", "text": "Mortgage broker fees are finance charges."}],
    )
    assert "Question:\nWhat rule does Loan state?" in body
    assert "Answer:\nThe rule is about broker fees." in body
    assert "Reference chunks:" in body
    assert "- reg-z-1026:s1:c01: Mortgage broker fees are finance charges." in body


def test_judge_rule_dispatches_to_the_configured_provider(monkeypatch):
    seen = {}

    class RecordingAdapter:
        def verdict(self, instructions, body):
            seen["instructions"] = instructions
            seen["body"] = body
            return {
                "passed": True,
                "citation": "reg-z-1026:s1:c01",
                "prompt_tokens": 5,
                "completion_tokens": 2,
            }

    monkeypatch.setattr(
        "app.generation.judge.judge_adapter", lambda settings=None, client=None: RecordingAdapter()
    )
    from app.generation.judge import judge_rule

    verdict = judge_rule(
        "What rule does Loan state?",
        "The rule is stated in reg-z-1026:s1:c01.",
        [{"chunk_id": "reg-z-1026:s1:c01", "text": "Mortgage broker fees are finance charges."}],
    )
    assert verdict == {
        "passed": True,
        "citation": "reg-z-1026:s1:c01",
        "prompt_tokens": 5,
        "completion_tokens": 2,
    }
    assert "reference chunk" in seen["instructions"].lower()
    assert "Reference chunks:" in seen["body"]


def test_the_bedrock_provider_builds_the_bedrock_adapter():
    assert isinstance(judge_adapter(_settings(judge_provider="bedrock")), BedrockJudgeAdapter)


def test_an_unregistered_provider_name_is_rejected_by_the_dispatch():
    from types import SimpleNamespace

    with pytest.raises(RuntimeError, match="unknown JUDGE_PROVIDER"):
        judge_adapter(SimpleNamespace(judge_provider="vibes"))


def test_the_bedrock_client_is_created_lazily_and_cached(monkeypatch):
    import sys
    import types as types_module

    calls = []
    fake_boto3 = types_module.ModuleType("boto3")

    def fake_client(name, region_name=None):
        calls.append((name, region_name))
        return object()

    fake_boto3.client = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    adapter = BedrockJudgeAdapter(
        _settings(judge_provider="bedrock", judge_model="anthropic.test:0", aws_region="eu-west-1")
    )

    first = adapter._bedrock()
    second = adapter._bedrock()
    assert first is second
    assert calls == [("bedrock-runtime", "eu-west-1")]
