"""The vendored candidate-v1 contract and its drift guard."""

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from app.candidate import run_candidate
from tests.parity_fixture import TASK_BY_ID, FixtureDeps

ROOT = Path(__file__).resolve().parents[1]
VENDORED = ROOT / "contract" / "candidate-v1.schema.json"
PINNED_SHA256 = "28d47b300f1d808e48d8d4532507194ce5032aa35015f5e3649bb604286a61b6"


def _normalized(path: Path) -> bytes:
    """Read a file with LF endings so the checksum does not depend on the checkout.

    Git can hand a Windows checkout CRLF while CI sees LF for the same blob; the
    contract bytes are what the harness owns, not the platform's line endings.
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


def _schema() -> dict:
    return json.loads(VENDORED.read_text(encoding="utf-8"))


def _harness_schema() -> Path | None:
    override = os.environ.get("EVAL_HARNESS_ROOT")
    base = Path(override) if override else ROOT.parent / "eval-harness"
    candidate = base / "contract" / "candidate-v1.schema.json"
    return candidate if candidate.is_file() else None


def test_the_vendored_schema_is_the_pinned_checksum():
    digest = hashlib.sha256(_normalized(VENDORED)).hexdigest()
    assert digest == PINNED_SHA256, (
        "contract/candidate-v1.schema.json changed; re-vendor the harness copy and "
        "update PINNED_SHA256 in this test"
    )


def test_the_vendored_schema_matches_the_harness_clone_when_present():
    upstream = _harness_schema()
    if upstream is None:
        pytest.skip("harness clone is not present; the pinned checksum is the guard")
    assert _normalized(VENDORED) == _normalized(upstream), (
        f"contract/candidate-v1.schema.json drifted from {upstream}; the harness owns "
        "the schema, so copy its file verbatim and update PINNED_SHA256"
    )


def test_the_schema_carries_the_v1_shapes():
    schema = _schema()
    assert schema["title"].startswith("eval-harness candidate contract")
    assert schema["$defs"]["request"]["required"] == [
        "case_id",
        "prompt",
        "context",
        "prompt_override",
        "seed",
    ]
    assert schema["$defs"]["response"]["required"] == ["answer"]


def test_a_request_validates_against_the_vendored_schema():
    task = TASK_BY_ID["t2-01"]
    request = {
        "case_id": "case-1",
        "prompt": task.prompt,
        "context": [{"chunk_id": "bank-1", "text": "Rule bank-1."}],
        "prompt_override": None,
        "seed": 7,
    }
    Draft202012Validator(_schema()).validate(request)


def test_a_candidate_response_validates_against_the_vendored_schema():
    task = TASK_BY_ID["t2-01"]
    request = {
        "case_id": "case-1",
        "prompt": task.prompt,
        "context": [{"chunk_id": "bank-1", "text": "Rule bank-1."}],
        "prompt_override": None,
        "seed": 7,
    }
    response = run_candidate(request, deps=FixtureDeps())
    Draft202012Validator(_schema()).validate(response)


def test_the_minimal_response_is_an_answer_only():
    Draft202012Validator(_schema()).validate({"answer": "42"})


def test_the_demo_harness_spec_points_at_this_candidate():
    path = ROOT / "eval" / "fixtures" / "parity" / "harness-spec.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert spec["spec_version"] == 1
    assert spec["seed"] == 77
    candidate = spec["candidates"][0]
    assert candidate["adapter"] == "evalharness.adapters.candidates.http:build"
    assert candidate["options"]["base_url"] == "http://127.0.0.1:8768"
    assert candidate["options"]["auth"] == "none"
    assert candidate["options"]["context"] == "rule"
