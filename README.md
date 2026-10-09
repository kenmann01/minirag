# Map Writes the Test

An evaluation harness that scores one task list three ways: the question
alone, the question with a code map, and the question with the map plus the
cited rule text. A call-scan skill maps a Java repository, the map generates
the task list, a jailed agent answers, and a judge grades the citations.
Postgres with pgvector stores the corpus chunks and every run's metrics;
Grafana shows the runs.

Public-domain corpora only: `corpora/banking` (12 CFR 1026, Regulation Z) and
`corpora/aviation` (FAA handbook). The demo repository mapped by the gate is
committed at `eval/demo-repo`.

## Setup

1. Start Postgres and Grafana:

   ```bash
   docker compose up -d
   ```

   Compose publishes Postgres on 5432 and Grafana on 127.0.0.1:3000. When the
   host already runs Postgres on 5432, copy
   `docker-compose.override.yml.example` to `docker-compose.override.yml` to
   publish on 5433 and set `DATABASE_URL` accordingly.

2. Install and start Ollama on the host, then pull the model:

   ```bash
   ollama serve
   ollama pull qwen3:8b
   ```

3. Create the environment:

   ```bash
   python -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   ```

## Use

Ingest the corpus (defaults to `CORPUS_DIR`, else `corpora/banking`):

```bash
python -m app ingest
```

Run the fixed ten-golden retrieval exam and write `eval/record.json`:

```bash
python -m app eval
python -m app eval --retriever vector --output eval/record-vector.json
```

Map a repository and record the gate numbers:

```bash
python -m app gate --repo eval/demo-repo
```

Score one task list three times (bare, map, map plus rules):

```bash
python -m app score --repo eval/demo-repo --graph eval/demo-graph.json \
    --output eval/runs --label "run name"
```

Watch a score run stream each call, or compare three written reports:

```bash
python -m app live      # http://127.0.0.1:8767
python -m app compare eval/runs   # http://127.0.0.1:8766
```

Validate the semantic model against the live database:

```bash
python -m app ossie validate
```

Seed the demo rig with labeled synthetic runs and gate history:

```bash
python scripts/seed_runs.py
```

Preflight compose, Grafana, and every board query:

```bash
python scripts/smoke_rig.py
```

The board is at http://127.0.0.1:3000/d/map-writes-the-test. Runs stored by
the seeder are labeled `origin=seeded`; never present them as model results.

## Tier-2 judge

The citation judge defaults to the local Ollama model. To swap it for AWS
Bedrock, set `JUDGE_PROVIDER=bedrock`, `JUDGE_MODEL=<bedrock model id>`,
install `boto3`, and provide credentials through the standard AWS
environment chain. No key material belongs in this repository. See
`CONFIG.md`.

## Layout

`app/corpus` (conversion, chunking, embeddings, ingest), `app/retrieval`
(hybrid search, bridge, reranker), `app/generation` (answer and judge model
adapters, prompts), `app/harness` (map, tasks, scoring, agent, metrics),
`app/storage` (database adapters), `app/observability` (OSSIE validation),
`app/web` (local demo pages). See `ARCHITECTURE.md`.
