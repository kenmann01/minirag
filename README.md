# Mini RAG

A command-line RAG assistant for the Version 2.0 employee expense policy.
Postgres with pgvector stores policy chunks; all-mpnet-base-v2 creates
embeddings; Qwen 3 generates grounded answers through Ollama. Each ask
retrieves policy sections through hybrid vector and keyword search fused with
reciprocal rank fusion, then reranks the fused candidates with a cross-encoder
before generation.

## Setup

1. Install and start Ollama on the host, then pull Qwen 3 8B:

   ```bash
   ollama serve
   ollama pull qwen3:8b
   ```

2. Start Postgres and pgvector:

   ```bash
   docker compose up -d
   ```

   Compose runs only Postgres. Ollama remains on the host.

3. Create the environment and install dependencies:

   ```bash
   python -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   ```

   The default Ollama URL is `http://host.docker.internal:11434` and the
   default generation model is `qwen3:8b`.

## Use

Ingest the policy:

```bash
python -m app ingest
```

Ask one question:

```bash
python -m app ask "How much can I spend on food each day?"
```

Run the fixed nine-golden exam, print the per-golden table, and write
`eval/record.json`:

```bash
python -m app eval
```

A/B the retriever by disabling the keyword lane (`vector` runs vector-only,
which is the Part 3 comparison arm):

```bash
python -m app eval --retriever vector --output eval/record-vector.json
```

The exam exits nonzero when any golden fails recall or its answer checks.
The goldens live in `eval/goldens.json`; the harness bypasses the question
cache so its numbers always measure the pipeline.
