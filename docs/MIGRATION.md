# Migration: where the evaluation harness went

Issue #16 split the old `map-writes-the-test` repository into two products.
This repository keeps retrieval, generation, the corpus and the candidate-v1
endpoint. Everything that scored a run, and the OSSIE graph materialisation
that fed the old gate, was removed and lives in
[kenmann01/eval-harness](https://github.com/kenmann01/eval-harness). The
candidate contract version is **candidate-v1**, vendored read-only at
`contract/candidate-v1.schema.json`.

| Before (this repository) | Now |
| --- | --- |
| `app/harness/score.py` (three-arm scoreboard, price triple, metrics sink) | harness `evalharness.core` (spec, planner, executor, scorer, cost) and `evalharness.storage` |
| `app/harness/tasks.py`, `app/harness/graph.py`, `app/harness/adapter.py` (task generation, edge selection, Fineract tree chooser) | harness candidate/example code; nothing stayed here |
| `app/observability/` (graph loader, OSSIE materialisation and validation) and `ossie/` | removed with the harness; the issue asked for graph materialisation to leave this repository |
| `app/harness/metrics.py` (runs, task_results, tool_calls, gate_metrics) | harness schema and repository; this repository no longer writes run rows |
| `app/harness/gate.py` and `skills/call-scan/` (repository scanner, scale record) | removed; the harness owns evaluation mechanics |
| `app/harness/agent.py` (jailed repo agent) | harness candidate adapter; this repository's `ollama_base_url` moved to `app/generation/ollama.py` |
| `app/harness/evaluate.py` (golden retrieval exam) | stayed here as `app/retrieval/exam.py`; it is retrieval, not scoring |
| `app/web/live.py`, `app/web/compare.py` | harness console (`evalharness.api`) |
| `grafana/`, `scripts/build_dashboard.py`, `scripts/seed_runs.py`, `scripts/smoke_rig.py` | deleted; results live in the harness console |
| `eval/demo-repo/`, `eval/demo-graph.json`, `eval/fineract-gate.json` | deleted with the scanner and the gate |
| `eval/fixtures/parity/` | kept as provenance; the drift guard now runs in the harness repository |
| `pydantic-evals` dependency | dropped with `score.py`; `pydantic-ai` stays for the Ollama judge |

## Commands

Kept: `python -m app ingest`, `eval`, `retrieve`, `candidate`, `ossie`.
Removed: `score`, `gate`, `live`, `compare`. To score a run, point the
harness at `python -m app candidate --serve`; see `docs/CANDIDATE.md`.

## Models and data

The harness owns cases, prompt variants, context settings, metrics, judges,
price tables and run storage. This repository owns the corpus, ingestion,
hybrid retrieval, the answer prompt, the judge adapters, and the transport
that answers a v1 request. Its `run_candidate` never scores itself and never
reads a spec.

Historical records in `docs/prd.md`, `docs/audit-findings.md` and
`CHANGELOG.md` describe the repository before the split; they are records,
not runbooks.
