# Entry points

Where work actually starts, and why a later agent would open that file first.

## Run here

- `python -m app ingest` — replace `policy_chunks` from markdown. Implementation: `app/cli.py` → `app/ingest.py`.
- `python -m app ask "..."` — one grounded answer. Same file; cache, search, rerank, generate.
- `python -m app eval` — This is old, and not part of eval harness. Loads `eval/goldens.json` and runs each Minion question through the same search the ask path uses. Search reads `policy_chunks`. Writes `eval/record.json` and exits 1 if any golden fails.
- `python -m app retrieve "..."` — chunk JSON, no generation. Open `app/bridge.py` to see why the reranker is skipped.
- `python -m app score --repo --graph --output` — the three-run scoreboard. Open `app/score.py`.
- `python -m app compare <report_dir>` — scoreboard page on port 8766 (`app/compare.py`).
- `python -m app serve` — ask-trace page on port 8765 (`app/serve.py`).
- `python -m app live` — live score calls on port 8767 (`app/live.py`).
- `python -m app gate --repo` — Graphify scale record (`app/gate.py`).
- `python -m app ossie materialize|validate` — graph tables and the OSSIE map (`app/ossie.py`, `ossie/map-writes-the-test.yaml`).
- `python convert_docs <pdf_dir> --out <dir>` — PDF to heading markdown. The extensionless launcher imports `convert_docs.py`, which calls `app/convert.py`.
- `python scripts/smoke_rig.py` — compose, Grafana health, and panel SQL.
- `docker compose up -d` — Postgres and Grafana only (`docker-compose.yml`).
- `pytest` — fast suite; the `llm` marker is off (`pytest.ini`). CI's full tier is `workflow_dispatch` (`.github/workflows/ci.yml`).

## Start-here files

- `app/__main__.py` — module entry; it only calls `app.cli.main`.
- `app/cli.py` — every subcommand and which dependencies it constructs.
- `docs/demo-start.md` — talk commands. `README.md` stops at ingest, ask, serve, and eval.
- `docs/prd.md` — what the three runs and the corpus swap are for.

## Surfaces

- CLI: `python -m app` with ingest, ask, eval, retrieve, score, compare, serve, live, gate, ossie.
- HTTP: stdlib servers in `app/serve.py` (8765), `app/compare.py` (8766), `app/live.py` (8767); Grafana on 127.0.0.1:3000.
- Workers / jobs: none.
- Scripts: `convert_docs` and `scripts/smoke_rig.py`. `build_submission.py` builds a submission bundle; it is not the runtime.
