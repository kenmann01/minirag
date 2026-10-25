# Architecture

This repository is the RAG side of the evaluation split: corpus ingestion,
hybrid retrieval, grounded generation, one fixed retrieval exam, OSSIE graph
materialisation and validation, and the candidate-v1 HTTP seam. The evaluation
harness that scores runs lives in
[kenmann01/eval-harness](https://github.com/kenmann01/eval-harness).
`docs/MIGRATION.md` records what moved. Domain terms live in `docs/prd.md`.

## Map

```mermaid
flowchart TD
  cli["python -m app"] --> ingest
  cli --> eval
  cli --> retrieve
  cli --> candidate
  cli --> ossie

  ingest["ingest"] --> chunk["corpus.chunking"]
  chunk --> embed["corpus.embeddings"]
  embed --> pg[("Postgres pgvector")]

  eval["retrieval.exam"] --> search["retrieval.search"]
  retrieve["retrieval.bridge"] --> search
  eval --> rerank["retrieval.reranker"]
  search --> pg

  eval --> gen["generation.generate + validate"]
  gen --> ollama["generation.ollama"]
  gen --> judge["generation.judge (ollama | bedrock)"]

  candidate["candidate (v1)"] --> search
  candidate --> gen
  ossie["observability.ossie"] --> graph["observability.graph"]
  ossie --> pg
```

## Packages

- `app/corpus` — PDF conversion (`convert.py`), section chunking
  (`chunking.py`), embeddings (`embeddings.py`), and ingest (`ingest.py`),
  which replaces `policy_chunks` and stamps the embedding model on every row.
- `app/retrieval` — hybrid vector+keyword search fused by reciprocal rank
  (`retrieve.py`), the retrieve-only bridge the candidate uses (`bridge.py`),
  the cross-encoder reranker for the exam path (`reranker.py`), and the fixed
  golden exam (`exam.py`).
- `app/generation` — the grounded answer path (`generate.py`, `validate.py`,
  `schemas.py`, `ollama.py`), the answer prompt
  (`prompts/policy_answer_v3.md`), and the judge adapters (`judge.py`):
  `OllamaJudgeAdapter` by default, `BedrockJudgeAdapter` when
  `JUDGE_PROVIDER=bedrock`.
- `app/storage` — `db.py` protocol and `pgadapter.py` implementation.
- `app/observability` — the neutral graph loader (`graph.py`) and OSSIE
  materialisation plus live validation (`ossie.py`).
- `app/candidate.py` — the candidate-v1 seam. It imports retrieval and
  generation lazily so the module stays cheap to import.
- `app/cli.py` + `app/config.py` — dispatch and pydantic-settings
  configuration anchored at the repository root.

## Runtime

`ingest` replaces the stored chunk rows. `retrieve` runs the hybrid search
and returns chunk JSON. `eval` runs the ten goldens through the same search
plus the reranker and the generator, then writes `eval/record.json`. The
candidate endpoint maps one v1 request onto the same retrieval and generation
path; it never scores itself. `ossie materialize` loads a graphify graph into
`graph_nodes` and `graph_edges`; `ossie validate` checks the pinned OSSIE map
against those tables in the live database.

## Boundaries

- No module under `app/` references the evaluation harness, and
  `tests/test_no_harness.py` enforces it.
- The harness owns scoring, prompt variants, prices and result storage; this
  repository only answers requests.
- `contract/candidate-v1.schema.json` is vendored read-only from the harness.
- The committed parity fixture under `eval/fixtures/parity/` is provenance for
  the three-arm scoreboard. The drift guard that runs it lives in the harness
  repository.

## Out of scope for this cache

Ollama and boto3 are host tools, not modules. Hugging Face supplies the
embedding and reranker weights at runtime. Fineract is an external repository;
nothing in this tree scans it any more.
