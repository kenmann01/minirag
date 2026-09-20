# Mini RAG

A command-line RAG assistant for the Version 2.0 employee expense policy.
Postgres with pgvector stores policy chunks; MiniLM creates embeddings; Qwen 3
generates grounded answers through Ollama.

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

Run all six required questions and write `tests/output.json`:

```bash
python -m app eval
```

Choose a different output path with `python -m app eval --output <path>`.
