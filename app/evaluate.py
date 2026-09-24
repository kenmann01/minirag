"""The fixed exam: nine golden cases with expected sections and answer facts."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

GOLDENS_PATH = Path(__file__).resolve().parents[1] / "eval" / "goldens.json"

Kind = Literal["answer", "hybrid_demo", "poisoned", "refusal"]


class Golden(BaseModel):
    id: str
    question: str
    expected: list[str]
    must_contain: list[str]
    must_not_contain: list[str]
    kind: Kind


def load_goldens(path: Path = GOLDENS_PATH) -> list[Golden]:
    return [Golden.model_validate(case) for case in json.loads(path.read_text(encoding="utf-8"))]
