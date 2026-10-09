# Entry points

Where work actually starts, and why a later agent would open that file first.

## Run here

- `python -m app ingest` — replace `policy_chunks` from markdown, subfolders
  included. Implementation: `app/cli.py` → `app/corpus/ingest.py`.
- `python -m app eval` — the ten-golden retrieval exam over `eval/goldens.json`.
  Writes `eval/record.json` and exits 1 if any golden fails recall or answer
  checks. Implementation: `app/harness/evaluate.py`.
- `python -m app retrieve "..."` — chunk JSON, no generation. Open
  `app/retrieval/bridge.py` to see why the reranker is skipped.
- `python -m app score --repo --graph --output [--label] [--no-metrics]` —
  the three-run scoreboard. Open `app/harness/score.py`.
- `python -m app compare <report_dir>` — scoreboard page on port 8766
  (`app/web/compare.py`).
- `python -m app live` — live score calls on port 8767 (`app/web/live.py`).
- `python -m app gate --repo` — call-scan scale record (`app/harness/gate.py`).
- `python -m app ossie materialize|validate` — graph tables and the OSSIE map
  (`app/observability/ossie.py`, `ossie/map-writes-the-test.yaml`).
- `python scripts/convert_docs.py <pdf_dir> --out <dir>` — PDF to heading
  markdown (`app/corpus/convert.py`).
- `python scripts/seed_runs.py` — labeled seeded runs and gate history
  through the real pipeline (stand-in agents and judges, `origin=seeded`).
- `python scripts/build_dashboard.py` — regenerate the committed Grafana
  board; edit this file, not the JSON.
- `python scripts/smoke_rig.py` — compose, Grafana health, and every board
  section query.
- `docker compose up -d` — Postgres and Grafana (`docker-compose.yml`).
- `pytest` — fast suite; the `llm` marker is off (`pytest.ini`). CI's full
  tier is `workflow_dispatch` (`.github/workflows/ci.yml`).

## Start-here files

- `app/__main__.py` — module entry; it only calls `app.cli.main`.
- `app/cli.py` — every subcommand and which dependencies it constructs.
- `docs/demo-start.md` — talk commands. `README.md` is the short tour.
- `docs/prd.md` — what the three runs and the corpus swap are for.

## Surfaces

- CLI: `python -m app` with ingest, eval, retrieve, score, compare, live,
  gate, ossie.
- HTTP: stdlib servers in `app/web/compare.py` (8766) and `app/web/live.py`
  (8767); Grafana on 127.0.0.1:3000.
- Workers / jobs: none.
- Scripts: `scripts/convert_docs.py`, `scripts/seed_runs.py`,
  `scripts/build_dashboard.py`, `scripts/smoke_rig.py`.
