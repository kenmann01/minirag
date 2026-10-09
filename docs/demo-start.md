# Demo start

The talk shows one repository, one corpus, and three scored runs. The
comparison page is the scoreboard slide; the Grafana board is the across-runs
view. Fill both from real `score` runs. Do not type the illustrative numbers,
and never present seeded rows as model results.

## Before the room

1. Start Postgres: `docker compose up -d`. On this machine `DATABASE_URL` is
   port 5433 via the gitignored compose override; fresh clones on a free 5432
   keep the default.
2. Pin the embedder. `.env` should set
   `EMBEDDING_MODEL=Alibaba-NLP/gte-modernbert-base`. Ingest stamps this name
   on every chunk and search refuses vectors from any other model.
3. Start Ollama and pull the pinned model: `ollama serve` then `ollama pull
   qwen3:8b`. The agent, the phraser, and the default judge use temperature 0
   and seed 77. No cloud key is required. To grade with Bedrock instead, set
   `JUDGE_PROVIDER=bedrock` and `JUDGE_MODEL` (see `CONFIG.md`).
4. Install Python dependencies from `requirements.txt`. The gate maps a
   repository with `skills/call-scan`; which tree it scans is decided by
   `app/harness/adapter.py`. Both are in the repo, not a separate install.
5. Ingest the banking corpus: `python -m app ingest` (defaults to
   `CORPUS_DIR=corpora/banking`).

## The four talk commands

Convert public PDFs. A document with no numbered sections exits nonzero and
writes no file:

```bash
python scripts/convert_docs.py corpora/pdfs --out corpora/banking
```

Retrieve, without the reranker. Chunk ids are logged on stderr:

```bash
python -m app retrieve "provisioning limits" --top-k 5 --json eval/retrieve.json
```

Score the same task list three times. The committed demo tree and its scan
graph are enough for a rehearsal:

```bash
python -m app gate --repo eval/demo-repo --output eval/fineract-gate.json
python -m app score --repo eval/demo-repo --graph eval/demo-graph.json \
    --output eval/runs --label "demo"
```

Open the comparison. It only reads the three report files:

```bash
python -m app compare eval/runs
```

Then open http://127.0.0.1:8766. The across-runs board is
http://127.0.0.1:3000/d/map-writes-the-test?kiosk.

## Scale gate

Run this before a real task list. The record is `eval/fineract-gate.json`:
wall time, node count, edge count, largest mermaid, and every error. The
committed record came from the committed demo tree (about 30 nodes, 21 edges,
under a tenth of a second). A full Apache Fineract checkout maps its loans
package the same way and says so in `scope`.

```bash
python -m app gate --repo /path/to/fineract --output eval/fineract-gate.json
```

## Live swap

Replace the corpus folder and re-ingest. Do not change code. Then regenerate
the task list with `score`. Tier 2 questions follow the new chunks.

```bash
CORPUS_DIR=corpora/aviation python -m app ingest
python -m app score --repo eval/demo-repo --graph eval/demo-graph.json \
    --output eval/runs-aviation --label "aviation corpus"
python -m app compare eval/runs-aviation
```

Banking markdown is a public slice of 12 CFR 1026. Aviation markdown is a
public chapter of FAA-H-8083-25. Sources and licenses are in
`corpora/MANIFEST.md`.

## Where Ossie sits

`ossie/map-writes-the-test.yaml` is the plug-and-play unit. It validates
against `ossie/osi-schema.json` (Apache Ossie 0.1.1), including the run
provenance columns. The slide can show the file. This phase does not export
it to another vendor.

## Slide outline

1. Proficiency lives in the context, not in the model.
2. The pipeline: map, corpus, task list, three runs, scoreboard.
3. Scale: the five numbers in `eval/fineract-gate.json`.
4. The comparison page and the across-runs board, filled from real runs.
5. The live swap: banking questions, then aviation questions, same code.
6. One future beat: the Ossie file is the unit other tools can read.
