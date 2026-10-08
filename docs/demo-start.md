# Demo start

The talk shows one repository, one corpus, and three scored runs. The comparison page is the scoreboard slide. Fill it from a real `score` run. Do not type the illustrative numbers.

## Before the room

1. Start Postgres: `docker compose up -d`. The app expects `DATABASE_URL` on port 5432.
2. Pin the embedder. `.env` should set `EMBEDDING_MODEL=Alibaba-NLP/gte-modernbert-base`. The retrieve command prints this name.
3. Start Ollama and pull the pinned model: `ollama serve` then `ollama pull qwen3:8b`. Generation and the judge use temperature 0 and seed 77. No cloud API key is required. The Ollama client uses a placeholder key because the local OpenAI-compatible endpoint demands a non-empty value.
4. Install Python dependencies from `requirements.txt`. Install the Graphify CLI (`graphifyy`) on the demo machine. It is not imported by the app.
5. Ingest the banking corpus: `CORPUS_DIR=corpora/banking python -m app ingest`.

## The four talk commands

Convert public PDFs. A document with no numbered sections exits nonzero and writes no file:

```bash
python convert_docs corpora/pdfs --out corpora/banking
```

Retrieve, without the reranker. Chunk ids are logged on stderr:

```bash
python -m app retrieve "provisioning limits" --top-k 5 --json eval/retrieve.json
```

Score the same task list three times. This needs a Graphify `graph.json` from the gate and a checkout of the mapped repository:

```bash
python -m app score --repo /path/to/repo --graph /path/to/graph.json --output eval/runs
```

Open the comparison. It only reads the three report files:

```bash
python -m app compare eval/runs
```

Then open http://127.0.0.1:8766.

## Scale gate

Run this before a real task list. The record is `eval/fineract-gate.json`: wall time, node count, edge count, largest mermaid, and every error. If the full Apache Fineract map does not cover `loanaccount`, the command maps that module and says so in `scope`.

```bash
python -m app gate --repo /tmp/fineract --output eval/fineract-gate.json
```

## Live swap

Replace the corpus folder and re-ingest. Do not change code. Then regenerate the task list with `score`. Tier 2 questions follow the new chunks.

```bash
CORPUS_DIR=corpora/aviation python -m app ingest
python -m app score --repo /path/to/repo --graph /path/to/graph.json --output eval/runs-aviation
python -m app compare eval/runs-aviation
```

Banking markdown is a public slice of 12 CFR 1026. Aviation markdown is a public chapter of FAA-H-8083-25. Sources and licenses are in `corpora/MANIFEST.md`.

## Where Ossie sits

`ossie/map-writes-the-test.yaml` is the plug-and-play unit. It validates against `ossie/osi-schema.json` (Apache Ossie 0.1.1). The slide can show the file. This phase does not export it to another vendor.

## Slide outline

1. Proficiency lives in the context, not in the model.
2. The pipeline: map, corpus, task list, three runs, scoreboard.
3. Scale: the five numbers in `eval/fineract-gate.json`.
4. The comparison page, filled from `eval/runs`.
5. The live swap: banking questions, then aviation questions, same code.
6. One future beat: the Ossie file is the unit other tools can read.

This is old, and not part of eval harness. The ten-question Minion exam is unchanged: `python -m app eval` loads `eval/goldens.json` and runs each question through the same search the ask path uses. Search reads `policy_chunks`.
