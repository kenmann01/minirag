"""Real-generation tests. Deselected by default; the full CI tier runs them."""

import json

import pytest

from app.cli import main
from app.ingest import run
from app.pgadapter import PgAdapter

pytestmark = pytest.mark.llm


def test_the_real_generator_passes_the_golden_exam(tmp_path):
    adapter = PgAdapter()
    run(adapter)
    output_path = tmp_path / "record-real.json"

    exit_code = main(["eval", "--output", str(output_path)])

    record = json.loads(output_path.read_text())
    assert exit_code == 0
    assert record["summary"]["passed"] == record["summary"]["total"]
    assert record["summary"]["recall_hits"] == record["summary"]["total"]
