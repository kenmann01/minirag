# Entry points

Where work actually starts, and why a later agent would open that file first.

## Run here

- `python -m app ingest` — replace `policy_chunks` from markdown, subfolders
  included. Implementation: `app/cli.py` → `app/corpus/ingest.py`.
- `python -m app eval` — the ten-golden retrieval exam over `eval/goldens.json`.
  Writes `eval/record.json` and exits 1 if any golden fails recall or answer
  checks. Implementation: `app/retrieval/exam.py`.
- `python -m app retrieve "..."` — chunk JSON, no generation. Open
  `app/retrieval/bridge.py` to see why the reranker is skipped.
- `python -m app candidate --serve --port 8768` — the candidate-v1 endpoint
  (`app/candidate.py`). `--request REQ --response OUT` runs one file instead.
- `python scripts/convert_docs.py <pdf_dir> --out <dir>` — PDF to heading
  markdown (`app/corpus/convert.py`).
- `docker compose up -d` — Postgres only (`docker-compose.yml`).
- `pytest` — fast suite; the `llm` marker is off (`pytest.ini`). CI's full
  tier is `workflow_dispatch` (`.github/workflows/ci.yml`).

## Start-here files

- `app/__main__.py` — module entry; it only calls `app.cli.main`.
- `app/cli.py` — every subcommand and which dependencies it constructs.
- `docs/CANDIDATE.md` — the v1 contract and the candidate mapping.
- `docs/MIGRATION.md` — where the evaluation harness went.
- `docs/prd.md` — the domain onboarding document.

## Surfaces

- CLI: `python -m app` with ingest, eval, retrieve, candidate.
- HTTP: the candidate-v1 endpoint on 127.0.0.1:8768.
- Workers / jobs: none.
- Scripts: `scripts/convert_docs.py`.
- The evaluation harness (specs, scoring, console) is a separate repository:
  [kenmann01/eval-harness](https://github.com/kenmann01/eval-harness).
