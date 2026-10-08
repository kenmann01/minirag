# Config

How configuration is loaded, and which file wins. Point at committed examples; cache what they do not confess.

## Files

- `.env.example` — committed template. Copy to `.env`.
- `.env` — local overlay, gitignored. Not loaded by Docker Compose.
- `app/config.py` — `Settings` via pydantic-settings (`env_file=".env"`, `extra="ignore"`).
- `docker-compose.yml` — Postgres user/db `minirag` and Grafana admin/anonymous. Does not read `.env`.
- `docker-compose.override.yml` — gitignored machine overlay. The demo rig uses it to publish Postgres on 5433 (`docs/prd.md` Appendix A).
- `sql/001_init.sql` — first-boot `policy_chunks` only. Later tables are created in Python (`app/ingest.py`, `app/cache.py`, `app/metrics.py`, `app/ossie.py`).
- `prompt_v3.md` — generation prompt. Its stem is part of the ask cache key (`app/cache.py`).

## Override order

1. Field defaults on `Settings` (`app/config.py`).
2. `.env` in the process working directory.
3. Process environment variables, which beat `.env`. `ingest` also reads `CORPUS_DIR` from the environment in `app/ingest.py` when the CLI passes no folder.

## Required vs optional

`database_url` has no default; the process will not boot without `DATABASE_URL`. Everything else in `.env.example` already has a code default: embedder `Alibaba-NLP/gte-modernbert-base`, `qwen3:8b`, `OLLAMA_HOST=http://host.docker.internal:11434`, empty `CORPUS_DIR` (then `Policy/`), and the three local accounting rates. An empty `CORPUS_DIR` falls through to `Policy/`. This is old, and not part of eval harness: that folder is the Minion policies, and `python -m app eval` loads `eval/goldens.json` and runs each question through the same search the ask path uses. Search reads `policy_chunks`. Set `CORPUS_DIR` to `corpora/banking` or `corpora/aviation` for the talk corpus (`docs/demo-start.md`).

## Secrets

Local Postgres and Grafana passwords are the demo defaults in `docker-compose.yml` and `.env.example`, not a cloud secret. Ollama needs no API key; `docs/demo-start.md` notes the OpenAI-compatible client still sends a placeholder. Do not commit `.env`.
