# Mini RAG

A command-line RAG assistant for the Version 2.0 employee expense policy.
Postgres with pgvector stores policy chunks; MiniLM creates embeddings; Mistral
generates grounded answers through Ollama.

## Setup

1. Install and start Ollama on the host, then pull Mistral:

   ```bash
   ollama serve
   ollama pull mistral
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
   default generation model is `mistral`.

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
