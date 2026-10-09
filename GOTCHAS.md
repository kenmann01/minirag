# Gotchas

Landmines. Unwritten convention, surprising coupling, local-vs-container traps, generated code, dual layouts. Each item: the trap, the path that proves it, what to do instead.

## Landmines

- The committed Ollama URL is a Docker hostname. Evidence: the default in
  `app/config.py` is `http://host.docker.internal:11434`, while the app runs
  on the host. CI's full job sets `http://127.0.0.1:11434`
  (`.github/workflows/ci.yml`). Instead: point `OLLAMA_HOST` at the host's
  Ollama when Python is not inside a container.
- Two Ollama clients, two URL shapes. Evidence: `app/generation/ollama.py`
  posts to `{host}/api/chat`; `app/harness/agent.py` appends `/v1` for
  pydantic-ai. Instead: keep `OLLAMA_HOST` as the server root, without `/v1`
  or `/api`.
- Embeddings are 768-dimensional and not interchangeable. Evidence:
  `vector(768)` in `app/corpus/ingest.py` and `sql/001_init.sql`, and the
  model stamp on every chunk row. Instead: pin `EMBEDDING_MODEL`, re-ingest
  after any change, and expect `retrieval.retrieve` to refuse to rank
  vectors stamped with a different model until you do.
- Ingest replaces the corpus. Evidence: `app/corpus/ingest.py` deletes stale
  chunk and section rows. Instead: set `CORPUS_DIR` and run ingest again for
  a swap; do not expect both corpora to stay loaded.
- The exam table is order-sensitive. Evidence: `EXPECTED_EXAM` in
  `tests/test_evaluate.py` mirrors `eval/goldens.json` id for id. Instead:
  edit the golden file and the test in the same change.
- `retrieve` is not `ask` without the printer. Evidence:
  `app/retrieval/bridge.py` skips the reranker because rerank keeps one
  chunk per section. Instead: use eval when you need the generation path.
- Init SQL does not create the demo schema. Evidence: `sql/001_init.sql`
  runs only on an empty volume. Metrics and graph tables appear after score,
  gate, or `ossie materialize`; `ossie validate` fails until they exist.
- `mmdc` is external. Evidence: `app/harness/gate.py` records an error when
  the mermaid renderer is missing. The map itself is the in-repo call-scan
  skill (`skills/call-scan/scripts/scan.py`); `app/harness/adapter.py`
  chooses the tree. Instead: install `mmdc` on the host when a gate run must
  render SVG.
- The board JSON is generated. Evidence: `tests/test_rig_files.py` rebuilds
  it from `scripts/build_dashboard.py` and fails on drift. Instead: edit the
  builder and run it; never hand-edit
  `grafana/dashboards/map-writes-the-test.json`.
- Seeded rows are synthetic. Evidence: `scripts/seed_runs.py` stores
  `origin=seeded` and backdates its rows to spread the trend lines. Instead:
  always read a run's origin before quoting its numbers.
- `pytest` hides the live generator. Evidence: `addopts = -m "not llm"` in
  `pytest.ini`. Instead: `pytest -m llm` or the manual CI full workflow.
- `pytest` redirects `DATABASE_URL` to `minirag_test`. Evidence:
  `tests/conftest.py` creates the sibling database and points the suite at
  it because the tests drop and rebuild metric tables. Instead: point the
  dev rig at the original database for real runs, and never expect suite
  runs to preserve stored metrics.

## Layout tricks

`eval/record*.json` and `docker-compose.override.yml` are gitignored
(`.gitignore`). `scratch/` holds local seeder output and is ignored. The
committed demo tree `eval/demo-repo` mirrors Fineract's loans package layout
so the adapter recognizes it.

## Local vs deployed

Committed Compose publishes Postgres on 5432 and Grafana on 127.0.0.1:3000.
A host whose 5432 is taken copies `docker-compose.override.yml.example`
(gitignored once copied) to publish on 5433, which is what `.env` on this
machine uses. `DATABASE_URL` must match whichever port is actually
published. There is no cloud deploy in this repo.
