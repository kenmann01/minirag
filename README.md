# Mini RAG

Retrieval and grounded generation over a policy corpus. Postgres with pgvector
stores the corpus chunks; one CLI ingests, retrieves, answers, runs the fixed
retrieval exam, and serves the pipeline as an eval-harness candidate.

Public-domain corpora only: `corpora/banking` (12 CFR 1026, Regulation Z) and
`corpora/aviation` (FAA handbook).

The evaluation harness that used to live here now lives in
[kenmann01/eval-harness](https://github.com/kenmann01/eval-harness). This
repository keeps retrieval, generation, the corpus, the candidate-v1 endpoint,
and the vendored candidate contract (`contract/candidate-v1.schema.json`,
version `candidate-v1`). Scoring, prompt-variant matrices, price tables and
result dashboards are the harness's job. `docs/MIGRATION.md` maps what moved
where.

## Setup

1. Start Postgres:

   ```bash
   docker compose up -d
   ```

   Compose publishes Postgres on 5432. When the host already runs Postgres on
   5432, copy `docker-compose.override.yml.example` to
   `docker-compose.override.yml` to publish on 5433 and set `DATABASE_URL`
   accordingly.

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

Return chunks without generating:

```bash
python -m app retrieve "What fees count as a finance charge?"
```

Validate the semantic model against the live database, or materialise a
graphify graph into the graph tables first:

```bash
python -m app ossie materialize --graph path/to/graph.json
python -m app ossie validate
```

Convert a PDF corpus into heading markdown:

```bash
python scripts/convert_docs.py <pdf_dir> --out <dir>
```

## Candidate endpoint

The RAG pipeline is exposed as an eval-harness candidate over the v1 HTTP
contract:

```bash
python -m app candidate --serve --port 8768
```

See [docs/CANDIDATE.md](docs/CANDIDATE.md) for the request/response mapping,
the vendored contract, and the parity fixture.

## Tier-2 judge

The citation judge defaults to the local Ollama model. To swap it for AWS
Bedrock, set `JUDGE_PROVIDER=bedrock`, `JUDGE_MODEL=<bedrock model id>`,
install `boto3`, and provide credentials through the standard AWS
environment chain. No key material belongs in this repository. See
`CONFIG.md`.

## Layout

`app/corpus` (conversion, chunking, embeddings, ingest), `app/retrieval`
(hybrid search, bridge, reranker, the golden exam), `app/generation` (answer
and judge model adapters, prompts), `app/storage` (database adapters),
`app/observability` (graph loading and OSSIE validation), and
`app/candidate.py` (the v1 candidate seam). See `ARCHITECTURE.md`.
