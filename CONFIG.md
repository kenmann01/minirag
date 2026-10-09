# Config

How configuration is loaded, and which file wins. Point at committed examples; cache what they do not confess.

## Files

- `.env.example` — committed template. Copy to `.env`.
- `.env` — local overlay, gitignored. Not loaded by Docker Compose.
- `app/config.py` — `Settings` via pydantic-settings. The `env_file` is
  anchored at the repository root, so the CLI works from any working
  directory.
- `docker-compose.yml` — Postgres user/db `minirag` and Grafana
  admin/anonymous. Does not read `.env`.
- `docker-compose.override.yml.example` — committed overlay that publishes
  Postgres on 5433 for hosts whose 5432 is taken. Copy to
  `docker-compose.override.yml` (gitignored) and set `DATABASE_URL` to match.
- `sql/001_init.sql` — first-boot `policy_chunks` only. Later tables are
  created in Python (`app/corpus/ingest.py`, `app/harness/metrics.py`,
  `app/observability/ossie.py`).
- `app/generation/prompts/policy_answer_v3.md` — the grounded-answer prompt.

## Override order

1. Field defaults on `Settings` (`app/config.py`).
2. `.env` at the repository root.
3. Process environment variables, which beat `.env`.

## Required vs optional

`database_url` has no default; the process will not boot without
`DATABASE_URL`. Everything else in `.env.example` already has a code
default: embedder `Alibaba-NLP/gte-modernbert-base`, `qwen3:8b`,
`OLLAMA_HOST=http://host.docker.internal:11434`, `CORPUS_DIR=corpora/banking`,
and the three local accounting rates. `CORPUS_DIR` is resolved relative to
the repository root, so `corpora/aviation` swaps the corpus with no code
change; re-run ingest after any swap or embedder change (ingest replaces the
stored chunks and refuses to rank vectors from a different embedder).

## Judge provider

`JUDGE_PROVIDER` picks the tier-2 judge: `ollama` (default, local, seed 77)
or `bedrock`. Bedrock needs `JUDGE_MODEL` set to a Bedrock model id or
inference-profile arn (ids are account- and region-dependent, so nothing is
guessed), `pip install boto3`, and `AWS_REGION`. Credentials come from the
standard AWS environment chain (`AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`). No key material belongs in
this repository.

## Secrets

Local Postgres and Grafana passwords are the demo defaults in
`docker-compose.yml` and `.env.example`, not a cloud secret. Ollama needs no
API key. Bedrock credentials live only in the environment. Do not commit
`.env`.
