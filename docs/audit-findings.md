# Audit findings, 2026-10-09

Final audit of `feat/eval-harness` at commit 43b0333 plus the same-day repair
branch state. Every finding lists the evidence at the audited commit, what
changed, and what was deliberately left. Severity is the audit's, not the fix
order. Tests: 108 passed, coverage 86.9%, ruff and black clean.

## CRITICAL

### A1. Answer keys were printed into the graded prompts

Evidence: `app/tasks.py:350` rendered `target: {edge.target}` verbatim into the
map excerpt for every arm that showed the map, and `app/agent.py:72-73`
instructed the agent: "If the prompt already states the target symbol and its
file, reply with that symbol and file path and do not call tools." The map and
map+rules pass rates were therefore construction-guaranteed.

Fix: the excerpt carries the scaffold only (source, relation, file, module
diagram) and the instruction is neutral. `test_the_map_excerpt_carries_no_answer_key`
locks the contract. The module diagram may still show the callee as one node
among the module's members; that is the map doing its job, and on real-scale
modules it does not single the answer out.

### A2. The live trace fabricated its retrieval evidence

Evidence: `app/score.py:472` called `retrieve(task.prompt)`, discarded the
result, and emitted `task.expected["chunk_ids"]` as the "returned" chunks.

Fix: the emitted `returned`/`chunk_ids` are the ids retrieval actually
returned; the expected ids travel separately as `expected_ids`.

### A3. The tier-2 judge graded blind

Evidence: `app/score.py:133` sent the judge only the question, the answer, and
the allowed chunk ids; `LLMJudge`'s case inputs (`score.py:286`) carried no
chunk text either. A judge that always answered `passed=true` citing
`allowed[0]` passed any answer, and the tests hard-wired exactly that.

Fix: one evidence-based evaluator. The judge prompt includes the reference
chunk text (capped at 480 chars per chunk); the verdict must cite one of those
ids and the citation is re-checked against the allowed set. The blind
`LLMJudge` and `prepare_local_judge` are gone. `test_judge_rule_sends_question_answer_and_chunk_text`
and `tests/test_judge.py` lock it.

### A4. The repo could not pass its own CI

Evidence: commit 43b0333 deleted `Policy/` and `eval/goldens.json` while CI
(`ci.yml:79,149`), `app ingest`'s default corpus, `python -m app eval`, and six
test files still required them; `pytest` failed during collection.

Fix: the canonical corpus is `corpora/banking` (recursive ingest, default in
Settings), a fresh ten-golden exam was authored from the Regulation Z slice,
and CI's fast tier is green again with the new defaults.

## HIGH

### B1. Ground truth deleted but the new harness depended on it

The tier-2 bridge reads `policy_chunks`; with `Policy/` gone, only the local
Docker volume kept the harness runnable. Fix: same as A4 (committed corpus is
the default), plus `eval/demo-repo`, a committed Java tree mirroring Fineract's
loans package, so the gate and the seeder run from a fresh clone.

### B2. Gate self-certified loans coverage

Evidence: `app/gate.py:175-176` overwrote `loans_covered=True` whenever any
node existed. Fix: the real `graph.loans_covered` check decides.

### B3. Thread-unsafe global scoring state

Evidence: module-level `_JUDGE_TOKENS`/`_PRICES` mutated per arm while
`app/live.py` served each run in its own thread. Fix: per-run state moved into
a ContextVar set inside `run_score`.

### B4. Embedding-model drift was silent

Evidence: `policy_chunks` stored no model identity while queries embedded with
whatever Settings resolved. Fix: ingest stamps `embedding_model` on every row,
older tables are rebuilt, and search raises `EmbeddingModelMismatch` instead of
ranking foreign vectors.

### B5. Cost understated the judge

Evidence: only the citation judge's tokens reached the scoreboard; the separate
LLMJudge reported nothing (`score.py:211-212`), and a hardcoded
`Prices(0.15, 0.60, 0.001)` fallback duplicated config. Fix: one judge, whose
tokens all count, and prices come from Settings.

### B6. Unreproducible ports and a lying README

Evidence: `docker-compose.override.yml` (the 5433 fix) was gitignored,
`.env.example` pointed at 5432, `docs/demo-start.md` said 5432 while the demo
machine used 5433, and the README claimed Compose runs only Postgres while it
also starts Grafana. Fix: `docker-compose.override.yml.example` committed,
`.env.example` and every run doc state the real ports and the Grafana service.

### B7. Committed gate record contradicted every doc that quoted it

Evidence: `eval/fineract-gate.json` recorded 1026 nodes / 631 edges / 0.311 s
with a Unix `/tmp` path while the handbook, PRD, and demo docs promised
"21.70 s, 61983 nodes, 303539 edges". Fix: the record was regenerated from a
real scan of the committed demo tree (30 nodes, 21 edges, ~0.08 s, repo-relative
graph path), and the docs quote those numbers with the old scan kept as
history. The four big HTML docs carry a dated snapshot banner.

## MEDIUM

- Tier-1 grader forgave a missing file (`score.py:154-156`). Fixed: symbol and
  file are both required.
- Gate dead code: the 200-node render ceiling was unreachable below the 24-node
  cap, and every record carried the fake error "module mermaid diagrams are
  capped at 24 nodes". Both removed; `module_diagrams` now documents its
  4-tuple.
- `record_gate` crashed the CLI when Postgres was down after the gate file was
  already written. It now warns.
- `live` accepted arbitrary absolute `graph`/`repo` paths; both are now jailed
  to the server's working directory.
- Destructive tests: the suite dropped and rebuilt tables in the developer's
  configured database. `tests/conftest.py` now creates and points the suite at
  a sibling `minirag_test` database.
- Stale untracked artifacts (`eval/record*.json`, `eval/service-module.svg`,
  `scratch/seed_rig.py`, `app/static/index.html`) removed.
- The 18 "This is old, and not part of eval harness." provenance stamps are
  gone; the exam is documented as what it is.

## Removed on request

The old-RAG surface: `app ask`, `app serve`, the question cache, the ask-trace
module and page, `tests/test_rag.py`, `tests/test_trace.py`, and
`build_submission.py`. The ask path's pieces that the harness still uses
(search, reranker, generation, validation) remain under `app/retrieval` and
`app/generation` and are covered by the exam tests. All Minion references were
removed, including the `bananaphone` golden (now `out-of-scope`) and the
zagat-grade biscuit fixtures (now a neutral sprocket allowance).

## Deliberately left

- Real-LLM runs were not generated: Ollama is no longer installed on this
  machine, and the board is populated by labeled seeded runs (`origin=seeded`,
  backdated to spread the trend lines; the seeder states this in its output).
- Handbook and PRD line-number citations predate the restructure; they carry a
  drift note instead of a line-by-line re-verification.
- The seeded runs backdate their rows for readable trends. This is presentation
  of synthetic data only and is documented in the seeder, the board
  descriptions, and `docs/demo-rig.md`.
- The Mermaid renderer `mmdc` remains a host tool; the gate records its
  absence instead of failing.
- CI's full tier (`app eval` + `pytest -m llm` against real Ollama) was not
  dispatched; it needs a runner with Ollama or a local `pytest -m llm`.

## Verification

- `pytest`: 108 passed, coverage 86.9% (floor 80%).
- `ruff check` and `black --check`: clean across `app/ scripts/ tests/`.
- `python scripts/smoke_rig.py`: all steps PASS against 127.0.0.1:5433/minirag.
- Every board panel query executed against the seeded database (0 failures).
- Board reviewed in a browser at 1920x1080 kiosk mode; screenshots in
  `D:\Genspark\minirag-board-shots\board-final-{1-top,2-mid,3-bottom}.png`.
