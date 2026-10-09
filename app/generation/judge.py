# Internal and Confidential - Not for External Distribution.
"""LLM-as-judge adapters for the tier-2 citation check.

``judge_rule`` grades one answer against the reference chunks. The judge
itself is pluggable: ``JUDGE_PROVIDER=ollama`` (default) uses the local
Ollama model, ``JUDGE_PROVIDER=bedrock`` uses the AWS Bedrock converse API.
Bedrock credentials are read from the standard AWS environment chain
(``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``, ``AWS_SESSION_TOKEN``,
``AWS_REGION``); no key material lives in this repository. Bedrock also
needs an explicit ``JUDGE_MODEL`` (a Bedrock model id or inference-profile
arn) because ids are account- and region-dependent.
"""

import json
import re
from typing import Protocol

from pydantic import BaseModel

from app.config import Settings, get_settings


class CitationVerdict(BaseModel):
    """What the judge must return: one pass flag and one cited chunk id."""

    passed: bool
    citation: str


class JudgeAdapter(Protocol):
    """One graded verdict per question/answer/references call."""

    def verdict(self, instructions: str, body: str) -> dict:
        """Return ``passed``, ``citation``, and token counts for one grading."""
        ...


DEFAULT_INSTRUCTIONS = (
    "Cite exactly one chunk id from the reference chunks. "
    "passed is true only when that reference chunk's text supports the answer."
)


def reference_body(prompt: str, answer: str, references: list[dict]) -> str:
    """Build the grading prompt body shared by every judge provider."""
    chunks = "\n".join(
        f"- {chunk.get('chunk_id')}: {str(chunk.get('text') or '')[:480]}".rstrip()
        for chunk in references
    )
    return f"Question:\n{prompt}\n\nAnswer:\n{answer}\n\nReference chunks:\n{chunks}"


def parse_verdict_text(text: str) -> CitationVerdict:
    """Parse the judge's JSON reply, tolerating surrounding prose or fences."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"judge reply was not JSON: {text[:200]}")
    return CitationVerdict.model_validate(json.loads(text[start : end + 1]))


class OllamaJudgeAdapter:
    """The default local judge: the configured Ollama model, pinned to seed 77."""

    def __init__(self, settings: Settings):
        self._settings = settings

    def verdict(self, instructions: str, body: str) -> dict:
        """Run one grading through the local Ollama chat endpoint."""
        from pydantic_ai import Agent
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider

        from app.harness.agent import ollama_base_url

        model = OllamaModel(
            self._settings.ollama_model,
            provider=OllamaProvider(base_url=ollama_base_url(self._settings.ollama_host)),
            settings={"temperature": 0, "seed": 77, "thinking": False},
        )
        agent = Agent(model, instructions=instructions, output_type=CitationVerdict)
        result = agent.run_sync(body)
        usage = result.usage
        return {
            "passed": bool(result.output.passed),
            "citation": result.output.citation,
            "prompt_tokens": int(usage.input_tokens),
            "completion_tokens": int(usage.output_tokens),
        }


class BedrockJudgeAdapter:
    """Judge through the AWS Bedrock converse API.

    The boto3 client is created lazily so the rest of the rig works without
    boto3 installed, and so tests can inject a fake converse client.
    """

    def __init__(self, settings: Settings, client=None):
        self._settings = settings
        self._client = client

    def _model_id(self) -> str:
        model = self._settings.judge_model.strip()
        if not model:
            raise RuntimeError(
                "JUDGE_PROVIDER=bedrock needs JUDGE_MODEL set to a Bedrock model id "
                "(for example anthropic.claude-3-5-haiku-20241022-v1:0) or an "
                "inference-profile arn; ids are account- and region-dependent."
            )
        return model

    def _bedrock(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - depends on host env
                raise RuntimeError(
                    "the bedrock judge needs boto3; install it with 'pip install boto3'"
                ) from exc
            self._client = boto3.client(
                "bedrock-runtime", region_name=self._settings.aws_region or None
            )
        return self._client

    def verdict(self, instructions: str, body: str) -> dict:
        """Run one grading through Bedrock's converse API."""
        response = self._bedrock().converse(
            modelId=self._model_id(),
            system=[{"text": instructions}],
            messages=[{"role": "user", "content": [{"text": body}]}],
            inferenceConfig={"temperature": 0.0, "topP": 1.0},
        )
        text = "".join(
            part.get("text", "")
            for part in response.get("output", {}).get("message", {}).get("content", [])
        )
        verdict = parse_verdict_text(text)
        usage = response.get("usage", {})
        return {
            "passed": verdict.passed,
            "citation": verdict.citation,
            "prompt_tokens": int(usage.get("inputTokens", 0)),
            "completion_tokens": int(usage.get("outputTokens", 0)),
        }


def judge_adapter(settings: Settings | None = None, client=None) -> JudgeAdapter:
    """Build the judge named by ``JUDGE_PROVIDER`` (ollama or bedrock)."""
    settings = settings or get_settings()
    if settings.judge_provider == "bedrock":
        return BedrockJudgeAdapter(settings, client=client)
    if settings.judge_provider == "ollama":
        return OllamaJudgeAdapter(settings)
    raise RuntimeError(f"unknown JUDGE_PROVIDER {settings.judge_provider!r}")


def judge_rule(prompt: str, answer: str, references: list[dict]) -> dict:
    """Ask the configured judge whether one reference chunk supports the answer.

    The judge sees the reference chunk text, not just their ids, so its
    verdict is grounded in evidence a reader can check. The returned
    citation is re-checked against the allowed ids by ``RuleCitation``.
    Tokens are included in the run cost.

    Args:
        prompt: The tier-2 question that was asked.
        answer: The agent's answer to grade.
        references: Chunk records with ``chunk_id`` and ``text``.

    Returns:
        A verdict dict with ``passed``, ``citation``, and token counts.
    """
    verdict = judge_adapter().verdict(
        DEFAULT_INSTRUCTIONS, reference_body(prompt, answer, references)
    )
    return {
        "passed": bool(verdict["passed"]),
        "citation": verdict["citation"],
        "prompt_tokens": int(verdict.get("prompt_tokens", 0)),
        "completion_tokens": int(verdict.get("completion_tokens", 0)),
    }
