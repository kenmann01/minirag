# Gotchas

Landmines. Unwritten convention, surprising coupling, local-vs-container traps, generated code, dual layouts. Each item: the trap, the path that proves it, what to do instead.

## Landmines

- The committed Ollama URL is a Docker hostname. Evidence: `OLLAMA_HOST` in `.env.example` and the default in `app/config.py` are `http://host.docker.internal:11434`, while the app runs on the host. CI's full job sets `http://127.0.0.1:11434` (`.github/workflows/ci.yml`). Instead: point `OLLAMA_HOST` at the host's Ollama when Python is not inside a container.
- Two Ollama clients, two URL shapes. Evidence: `app/ollama.py` posts to `{host}/api/chat`; `app/agent.py` appends `/v1` for pydantic-ai. Instead: keep `OLLAMA_HOST` as the server root, without `/v1` or `/api`.
- Embeddings are 768-dimensional and not interchangeable. Evidence: `vector(768)` in `app/ingest.py` and `sql/001_init.sql`. `docs/prd.md` Appendix A still says a local pin of `all-mpnet-base-v2` while `.env.example` and `Settings` name `gte-modernbert-base`. Instead: pin `EMBEDDING_MODEL` and re-ingest after any change; the ask cache key includes that name (`app/cache.py`).
- Ingest replaces the corpus. Evidence: `app/ingest.py` deletes stale chunk and section rows. Instead: set `CORPUS_DIR` and run ingest again for a swap; do not expect both corpora to stay loaded.
- `--include-superseded` is a planted defect, and those asks are not cached. Evidence: `app/cli.py`, `docs/part6-diagnosis.md`. Instead: leave the flag off for normal answers.
- `retrieve` is not `ask` without the printer. Evidence: `app/bridge.py` skips the reranker because rerank keeps one chunk per section. Instead: use ask or eval when you need the generation path.
- Init SQL does not create the demo schema. Evidence: `sql/001_init.sql` runs only on an empty volume. Metrics and graph tables appear after score, gate, or `ossie materialize`. `docs/demo-rig.md` says `ossie validate` fails until that seed exists.
- Graphify and `mmdc` are external. Evidence: `app/gate.py` looks for a `graphify` binary beside the interpreter (a venv symlink hides it) and records an error if `mmdc` is missing. Instead: install `graphifyy` on the host (`docs/demo-start.md`).
- `pytest` hides the live generator. Evidence: `addopts = -m "not llm"` in `pytest.ini`. Instead: `pytest -m llm` or the manual CI full workflow.
- `README.md` says Compose runs only Postgres. Evidence: `docker-compose.yml` also starts Grafana. The README also omits score, gate, retrieve, and ossie; those are in `docs/demo-start.md`.

## Layout tricks

`convert_docs` (no extension) and `convert_docs.py` are both the converter entry. `submission/`, `eval/record*.json`, and `docker-compose.override.yml` are gitignored (`.gitignore`). `scratch/seed_rig.py` is mentioned in `docs/demo-rig.md` and is not in the tree.

## Local vs deployed

Committed Compose publishes Postgres on 5432 and Grafana on 127.0.0.1:3000. The demo machine's gitignored override moves Postgres to 5433, which is what `docs/demo-rig.md` and `docs/prd.md` describe. `DATABASE_URL` must match whichever port is actually published. There is no cloud deploy in this repo.
