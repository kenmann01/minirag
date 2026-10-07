# The Map Writes the Test

Product Requirements Document. Domain onboarding for agents.

- **Project:** Domain Onboarding Pipeline
- **Owner:** Associate FDE, Forward Deployed Engineering
- **Status:** v0.2, statuses reconciled with code
- **Date:** 7 October 2026
- **Internal working document, v0.2**

---

## Table of contents

1. Summary
2. Background and problem
3. Goals and non-goals (3.1 Goals, 3.2 Non-goals)
4. Audience and success criteria (4.1 Demo arc)
5. System overview
6. Functional requirements
7. Interfaces and data flows (7.1 Repository to map, 7.2 Documents to retrieved chunks, 7.3 The bridge command, 7.4 The converter, 7.5 The harness interface)
8. Data model
9. Non-functional requirements
10. Milestones and gates
11. Risks and mitigations
12. Out of scope
13. Open questions
14. Appendix A: minirag baseline facts
15. Appendix B: Fineract gate protocol

---

## 1. Summary

This document specifies a measured demo of agent domain onboarding, called The Map Writes the Test. The system maps a repository into a knowledge graph, derives an evaluation task list from that graph, retrieves industry rules from a corpus, and scores the same agent three times: bare, with the map, and with the map plus retrieved rules. The scoreboard reports tasks passed, tool calls, and cost per run.

v0.1 called this document pre-implementation. That label is retired. The gate has run and passed at full repository scope (Appendix B), and the components v0.1 listed as unbuilt or partial (the converter, the bridge, the evaluation generator, the three-run harness) are built and merged on feat/eval-harness. Every status in section 6 has been reconciled against the code. What remains unbuilt this phase is the observability layer: a metrics sink with a Grafana board (FR-12), and the OSSIE contract materialized and validated live (FR-11 and FR-13). Every claim in this document is labeled with its true status.

### What changed in v0.2

- All functional requirement and workstream statuses reconciled against the code on feat/eval-harness at 65a2fad. The v0.1 labels PARTLY EXISTS, PENDING, and FUTURE are retired: each resolved to EXISTS, UNBUILT, or NOT TRIGGERED.
- The Fineract gate ran on 6 October 2026 and passed at full scope. The measured numbers are recorded in Appendix B and summarized in W0.
- FR-11 promoted from Could/FUTURE to Must for this phase: the skill-and-corpus contract ships as an OSSIE unit, validated live on stage.
- FR-12 and FR-13 added, both Must and UNBUILT: a Grafana board over a metrics sink, and the OSSIE tables materialized with a live validate command.
- NFR-7 added: the demo rig is local-first. Ollama runs as a native host service; Docker runs postgres+pgvector and Grafana only.
- W0 marked DONE with measured numbers. W7 and W8 added, and they are the critical path for the demo rig.
- Section 4.1 added: the demo arc, seven beats plus a new beat 8 for the board and the contract.
- Risks updated with the actual gate findings (the mermaid node cap, mmdc missing) and a new Grafana risk.
- Open questions: the repository, the banking slice, and the FAA handbook are decided. The judge provider defaults to local Ollama. The live-versus-recording question stays open.

## 2. Background and problem

Coding agents fail predictably on codebases they have never seen. They explore by trial, burn tool calls, and miss domain constraints that live outside the repository. The standard fix is more context, but context is usually assembled by hand, is unmeasured, and does not transfer between engagements.

An associate Forward Deployed Engineer performs this assembly manually in the first week of every engagement: walk the client system, build a mental map, learn the domain rules, then work. The proposed system mechanizes that job. The repository skill produces the map. The corpus layer supplies the industry rules. The evaluation harness proves the difference instead of asserting it.

The demo supports a company-wide presentation. The claim to defend is simple: proficiency lives in the context, not in the model. The measured gap between the three runs is the evidence.

## 3. Goals and non-goals

### 3.1 Goals

First, prove the map works on a real, large repository, not a toy. Second, prove the task list can be derived from the map itself, which removes the objection that a human cherry-picked easy tasks. Third, prove that industry knowledge enters through a swappable corpus folder, demonstrated live by swapping banking documents for aviation documents. Fourth, report score, tool calls, and cost per run, so the improvement claim carries a cost claim with it.

### 3.2 Non-goals

The system does not evaluate the software under test. It evaluates the agent that works on the software. Compliance test generation for the codebase is a stated roadmap item, not part of this build. The system does not use client-internal documents. The talk may be recorded and shared, so public documents only.

## 4. Audience and success criteria

The primary audience is a company-wide internal presentation of roughly thirty thousand people, mixed engineers, delivery leads, and leadership. The demo must survive a mixed room: one plain thesis, one live mechanism, one scoreboard.

Success is defined by four criteria. The map builds on the chosen repository within the agreed time budget. The tier 1 task list passes deterministic checks without human task selection. The three runs show a monotonic score improvement on the same task list. The live corpus swap changes the flavor of generated domain questions on screen, with the pipeline otherwise untouched.

Table 1. Target scoreboard shape (ILLUSTRATIVE numbers, not results)

| Run | Context | Tasks passed | Tool calls | Cost per run |
|---|---|---|---|---|
| RUN 1 | bare agent | 2 of 8 | 64 | $0.42 |
| RUN 2 | agent plus the map | 5 of 8 | 31 | $0.19 |
| RUN 3 | agent plus map plus rules | 8 of 8 | 18 | $0.11 |

Every figure in Table 1 is a placeholder that fixes the shape of the scoreboard. The harness replaces all of them with measured values. Cost per run is the sleeper metric: a lost agent explores expensively, a mapped agent goes direct.

### 4.1 Demo arc (seven beats)

The talk runs as seven beats, carried over from the demo plan, plus one new beat for this phase.

1. Cold start. The agent flails on a repo it has never seen.
2. Build the map. The skill walks the repo. Mermaid for humans, graph for the agent.
3. The map writes the evals. Tasks come from nodes and edges, not from us.
4. Run 1. Bare agent. No map, no rules.
5. Run 2. Agent plus the map.
6. Run 3. Agent plus the map plus retrieved industry rules.
7. Live swap. Banking corpus out, aviation corpus in. The evals change flavor on screen.
8. (New in v0.2.) The OSSIE contract validates live and the Grafana board shows the scoreboard.

## 5. System overview

The system has two input lanes and one measurement spine. The map lane reads a repository through the mapping skill and emits two representations of the same knowledge: mermaid diagrams for humans and a graphify graph for agents. The corpus lane converts public industry documents into heading-structured markdown, ingests them through minirag into Postgres with pgvector, and serves retrieval through a bridge command. The measurement spine consumes both lanes: an evaluation generator derives a two-tier task list, the harness runs the agent on that list three times with increasing context, and the scoreboard records the outcome. The measurement spine now ends in an observability board (Grafana) fed by a metrics sink, and the OSSIE contract describes the tables the board reads.

Figure 1 (docs/demo-pipeline.html) shows the end-to-end data flow: the map lane and the corpus lane feed one scoreboard. Blue boxes in the figure exist today: the map, the scoreboard, and now the converter, the bridge, and the evaluation generator. Dashed amber boxes are this phase's build: the metrics sink, the Grafana board, and the OSSIE contract. Dotted arrows mark two things: the live swap performed during the talk (banking corpus to aviation corpus) and the OSSIE contract validating the sink's tables.

## 6. Functional requirements

Table 2. Functional requirements with true status

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-1 | Map a target repository into a graph of nodes and edges, and emit module-level mermaid diagrams. | Must | EXISTS |
| FR-2 | Derive tier 1 graph-fact checks from map nodes and edges, without human task selection. | Must | EXISTS |
| FR-3 | Generate tier 2 domain questions grounded in retrieved corpus chunks, each citing its chunk ids. | Must | EXISTS |
| FR-4 | Convert public regulation PDFs into markdown with numbered section headings, and refuse silent empty output. | Must | EXISTS |
| FR-5 | Expose a retrieve-only command that returns chunk JSON for a query and logs every retrieved chunk. | Must | EXISTS |
| FR-6 | Run the same task list three times, bare, with the map, with the map and retrieved rules, recording tasks passed, tool calls, and cost per run. | Must | EXISTS |
| FR-7 | Rebuild the corpus index from a replaced corpus folder with no code change. | Must | EXISTS |
| FR-8 | Fix determinism: temperature 0, seed 77, pinned embedding model, deterministic rank fusion. | Must | EXISTS |
| FR-9 | Keep the judge provider-agnostic across at least two providers. | Should | EXISTS |
| FR-10 | If full-repo mapping fails the gate, map a single module, the loans module, as fallback scope. | Should | NOT TRIGGERED |
| FR-11 | Package the skill and corpus contract as an OSSIE-format unit, validated live. | Must (promoted from Could) | UNBUILT |
| FR-12 | Stand up a Grafana board over a metrics sink. The sink writes runs, task_results, tool_calls, and gate_metrics tables; the board shows scoreboard by tier, tool calls, cost per run, retrieval distances, and gate stats. | Must | UNBUILT |
| FR-13 | Materialize the OSSIE tables (graph_nodes and graph_edges from graph.json, sections from ingest) so the YAML contract is true, and ship the validate command. | Must | UNBUILT |

Notes on the reconciled statuses:

- FR-1: the mapping skill lives on the owner's work laptop; app/gate.py wraps the graphify CLI for gate runs.
- FR-2: app/tasks.py generate_tasks (tasks.py:92) derives tier 1 from graph edges. Selection is deterministic (graph.py:71-90): EXTRACTED call and import edges, loan-first ordering, limit 4. Zero human selection.
- FR-3: tier 2 questions are grounded in bridge-returned chunk ids. A task citing chunks the bridge never returned fails the whole run (tasks.py:198-204).
- FR-4: app/convert.py plus convert_docs.py. Emits "## N. Title" markdown and exits nonzero when a document yields zero sections (tests/test_convert.py).
- FR-5: app/bridge.py. Retrieve-only JSON with chunk ids and sources. Logs every retrieved chunk to stderr. The reranker is off by default.
- FR-6 (was PARTLY EXISTS): app/score.py runs the same task list three times (bare, map, map_rules) through pydantic-evals. ScoreboardReport reports tasks passed per tier, tool calls, and cost per run (cost formula at score.py:65-71).
- FR-8: temperature 0, seed 77 at agent.py:67, score.py:86/93/117, and ollama.py:36. RRF fusion with k=60 is deterministic.
- FR-9: the judge is the pydantic-evals LLMJudge pinned to local Ollama (set_default_judge_model, score.py:74-94). Swapping the provider is configuration, not code.
- FR-10: the gate passed at full-repo scope, so the loans-module fallback never engaged. Kept documented as the fallback.
- FR-11: exit evidence is an ossie/map-writes-the-test.yaml that declares only tables that exist (graph_nodes, graph_edges, sections materialized in Postgres), plus a live `python -m app ossie validate` that passes on stage.

Status meanings: EXISTS means built and in use today. UNBUILT means designed here, not implemented. NOT TRIGGERED means the condition never fired this phase and the item stays documented as the fallback. The v0.1 labels PARTLY EXISTS, PENDING, and FUTURE are retired: FR-6 completed, FR-10 resolved by the gate result, FR-11 promoted to Must. Table 5 uses the same meanings, plus DONE for the completed gate.

## 7. Interfaces and data flows

### 7.1 Data flow one: repository to map

The skill walks the repository, parses files, and produces nodes for files, classes, and functions, plus edges for imports, calls, and uses. The graph is the agent-facing representation. Mermaid diagrams are the human-facing representation, restricted to module level because whole-repository diagrams exceed the mermaid size ceiling. Node count, edge count, and wall time were measured at the gate run (Appendix B) and are slide content.

### 7.2 Data flow two: documents to retrieved chunks

Public PDFs enter the converter, which emits markdown whose sections use the heading format that minirag ingestion requires. Ingestion chunks each section into windows of 800 to 1200 characters with at least 120 characters of overlap, embeds each child window with a pinned 768-dimension model, and stores the parent section text alongside the vector and a full-text index in Postgres with pgvector. The bridge queries this store and returns chunk records. Ingestion is a full rebuild: upsert all current chunks, delete stale ones.

### 7.3 The bridge command

Built as app/bridge.py. The contract:

```
python -m app retrieve <query> [--top-k N] [--json OUT]
-> { query, model, chunks: [ { chunk_id, source_doc, section,
       section_title, effective_date, text, distance, rrf_score } ] }
```

The command is retrieval-only. It never generates. The reranker is off by default for logging, because it deduplicates by section and silently drops chunks.

### 7.4 The converter

Built as app/convert.py with the convert_docs entry point. The contract:

```
python convert_docs <pdf-dir> --out <md-dir>
emits: one .md per source, sections as '## N. Title'
fails loud: nonzero exit if a document yields zero sections
```

The loud failure is a requirement, not a courtesy. Ingestion returns zero chunks silently when the heading convention is missing, which would empty the index on a malformed corpus. The converter refuses to hand one over (tests/test_convert.py).

### 7.5 The harness interface

The harness consumes a task list and emits scoreboard rows. A task carries its tier, its prompt, its expected evidence, and its origin, either map coordinates or chunk ids, so every task is auditable back to its source. A scoreboard row carries the run identity, the context level, tasks passed per tier, tool calls, and cost per run.

This interface is implemented: the task generator lives in app/tasks.py, the scoring in app/score.py, and a compare page puts runs side by side.

## 8. Data model

Table 3. Retrieved chunk record (as produced by minirag search today)

| Field | Type | Meaning | Example |
|---|---|---|---|
| chunk_id | string | Child window identity, stable across rebuilds of the same corpus. | lending-rules:s12:c03 |
| source_doc | string | Markdown file the chunk came from. | lending-rules.md |
| section | string | Numbered section within the document. | 12 |
| section_title | string | Heading text of the parent section. | Provisioning limits |
| effective_date | string | Rule validity date when the source states one. | 2026-04-01 |
| text | string | Parent section body, returned instead of the small child window. | A lender shall... |
| distance | float | Cosine distance in the dense lane. | 0.31 |
| rrf_score | float | Reciprocal rank fusion score across both lanes. | 0.0317 |

The task item and the scoreboard row are owned by the harness and now have concrete instances: the generator emits task lists, and the harness writes scoreboard JSONs per run (tasks, bare, map, map_rules) to the output directory at runtime. None is committed to the repo; recorded runs happen on the demo rig. Their shape is fixed by section 7.5.

Two more schemas join the model this phase, both UNBUILT. The metrics sink writes four tables: runs, task_results, tool_calls, and gate_metrics. The OSSIE contract (ossie/map-writes-the-test.yaml) declares the tables the board reads: graph_nodes and graph_edges materialized from graph.json, and sections materialized from ingest. FR-13 covers the materialization.

## 9. Non-functional requirements

Table 4. Non-functional requirements

| ID | Requirement | Verification |
|---|---|---|
| NFR-1 | Determinism: identical query and corpus produce identical ranking and answers across reruns. | Rerun diff on recorded outputs |
| NFR-2 | No cloud API keys in the retrieval path. Embeddings, ranking, and storage run locally. | Configuration audit |
| NFR-3 | Public documents only in any corpus that appears in the talk or its recordings. | Corpus manifest review |
| NFR-4 | Swappable corpora with no code change, only a folder replacement plus re-ingest. | Live swap rehearsal |
| NFR-5 | Cost per run recorded for every scored run, derived from token and tool-call accounting. | Harness output inspection |
| NFR-6 | Demo-rig portability: the full stack runs from Docker plus a Python environment on the demo machine. | Cold start on demo machine |
| NFR-7 | Demo-rig topology is local-first: Docker Desktop runs postgres+pgvector and grafana only; Ollama runs as a native host service (never containerized this phase); the start checklist covers both. | Cold start on demo machine |

## 10. Milestones and gates

The gate ran first, and its numbers are on the record. Everything that depended on the gate outcome followed, and the remaining builds are the ones the demo rig still needs.

Table 5. Build plan, gate first

| ID | Workstream | Exit evidence | Status |
|---|---|---|---|
| W0 | Gate: run the skill on Apache Fineract. | Five numbers recorded: wall 21.697s, 61,983 nodes, 303,539 edges, largest module mermaid capped at 24 nodes, errors: module mermaid diagrams capped at 24 nodes and mermaid renderer mmdc not installed. Scope: full repo, loans module covered. Caveats: graph.json was ephemeral (/tmp) so it must be regenerated on the demo rig, and the mmdc leg was never exercised. | DONE |
| W1 | The bridge inside minirag. | app/bridge.py: retrieve-only command returns chunk JSON and logs chunk ids per query to stderr. | EXISTS |
| W2 | The converter. | app/convert.py plus convert_docs.py: numbered section headings, nonzero exit on zero sections (tests/test_convert.py). | EXISTS |
| W3 | Corpora curation. | Banking slice chosen: reg-z-1026, pairs with the loans module. Swap corpus chosen: FAA-H-8083-25 chapter 2. Public, manifest listed. | EXISTS |
| W4 | Evaluation generation. | Two-tier task list derived from map and corpus in app/tasks.py. Tier 1 all deterministic (graph.py:71-90). Chunk-id grounding enforced (tasks.py:198-204). | EXISTS |
| W5 | The three scored runs. | Harness and three-run protocol built (app/score.py, bare / map / map_rules), scoreboard JSON writer and compare page in place. Recorded live numbers happen on the demo rig. | EXISTS |
| W6 | The talk. | Slides, rehearsal, Docker start checklist, live swap rehearsal. | UNBUILT |
| W7 | Metrics sink plus Grafana board. | Sink writes runs, task_results, tool_calls, gate_metrics. Board panels per FR-12: scoreboard by tier, tool calls, cost per run, retrieval distances, gate stats. | UNBUILT |
| W8 | OSSIE materialization and live validation. | graph_nodes, graph_edges, and sections materialized; `python -m app ossie validate` passes live on stage. | UNBUILT |

W0 no longer blocks anything: the gate passed at full scope, so W4 and W5 proceeded and are built. W7 and W8 are the critical path for the demo rig: the board and the live contract validation are the only demo-critical builds left.

## 11. Risks and mitigations

Table 6. Risks, named before anyone asks

| Risk | Mitigation |
|---|---|
| Monorepo scale breaks the mapping skill. | Resolved by the gate: full-repo scope passed (Appendix B). The loans-module fallback stays documented but was NOT TRIGGERED. |
| Mermaid rendering fails past a few hundred nodes. | Confirmed at the gate: module mermaid diagrams are capped at 24 nodes, and the renderer mmdc is not installed. Module-level diagrams only, never a whole-repository diagram, and install mmdc on the demo rig. |
| Circularity objection: a model writes the tasks and a model grades them. | Two tiers reported separately. Tier 1 is deterministic script checking on graph facts. |
| Docker or model services fail on stage. | Start checklist, images and weights pulled before the talk, cold start rehearsed. The rig names its services: Ollama runs native on the host, Grafana and postgres+pgvector run as containers with images pre-pulled. |
| Grafana board fails or shows stale data. | Provisioning-as-code dashboards, datasource pinned to the metrics tables, images pulled before the talk. |
| Corpus licensing exposure in a recorded talk. | Public documents only, manifest reviewed in W3. |
| Retrieval misses the governing rule for a question. | Hybrid lanes with rank fusion, and every retrieved chunk logged so misses are visible, not hidden. |
| Embedding model mismatch between environments. | Pin EMBEDDING_MODEL in configuration. The local environment and the code default currently differ. |

## 12. Out of scope

Compliance test generation for the codebase itself, the eval-the-software product, is out of scope and appears only as a roadmap slide. Client-internal corpora are out of scope for licensing reasons. A web interface for the pipeline is out of scope; the interface is a CLI with three commands. Multi-language retrieval beyond English full-text search is out of scope for this phase.

## 13. Open questions

Table 7. Open questions with owners on the resolution path

| Question | Resolution path |
|---|---|
| Is Apache Fineract the demonstration repository? | DECIDED. The gate passed at full scope; Fineract is the repository. |
| Which banking document slice anchors tier 2? | CHOSEN. reg-z-1026, which pairs with the loans module. |
| Which FAA handbook is the swap corpus? | CHOSEN. FAA-H-8083-25, chapter 2. |
| Is the live swap performed live or shown as a recording? | STILL OPEN. Rehearse the live swap in the talk workstream and decide after the first rehearsal timing. |
| Which judge provider grades tier 2? | RESOLVED BY DEFAULT. The judge is pinned to local Ollama; swapping the provider is configuration. |

## 14. Appendix A: minirag baseline facts

Location: D:\Genspark\minirag, branch feat/eval-harness at 65a2fad. The application is 28 Python modules, about 3,200 lines, with 16 test files and 111 tests (81 need live Postgres, and 1 carries a live-LLM marker deselected by default). Continuous integration runs in two tiers: fast on every push with a pgvector service container and faked generation, and full on manual dispatch against Ollama with qwen3:8b.

Retrieval is hybrid: a dense lane of local sentence-transformer embeddings with cosine distance, plus a Postgres full-text lane ranked by ts_rank. The lanes fuse by reciprocal rank fusion with k equal to 60 and keep the top 20 candidates. An optional cross-encoder reranker, ms-marco-MiniLM-L-6-v2, deduplicates by section, which is why logging bypasses it.

Generation runs on qwen3:8b via native host Ollama, temperature 0, seed 77, and no cloud API keys anywhere. Storage is Postgres with pgvector in Docker on local port 5433 via the compose override, which stays machine-local. The embedding dimension is fixed at 768 by the table schema, and the local environment pins all-mpnet-base-v2 while the code default names gte-modernbert-base, so the model must be pinned explicitly.

The eval corpus today is ten markdown policy files with ten golden evaluation cases and persisted records.

The eval harness side is new since v0.1: pydantic-evals plus pydantic-ai drive a jailed repo agent with list_dir and read_file tools capped at 12 calls, a task generator derives the two-tier task list, scoreboard JSONs record each run, and a compare page puts runs side by side. The observability layer for this phase is Grafana over a metrics sink (FR-12).

## 15. Appendix B: Fineract gate protocol

Clone Apache Fineract. Run the mapping skill against it. Record the following, in order:

1. Wall time from start to finish.
2. Node count in the graph.
3. Edge count in the graph.
4. Largest mermaid diagram that renders.
5. Every error or limit hit along the way.

Pass means the map builds and the loans module is covered at module-level mermaid. Fail does not change the plan; it changes scope to the loans module alone. The five numbers become the scale slide of the talk.

### Result (recorded 6 October 2026)

The gate ran against full-repo scope and passed.

1. Wall time: 21.697 seconds.
2. Node count: 61,983.
3. Edge count: 303,539.
4. Largest mermaid: the "service" module at 24 nodes per diagram. 9,255 module nodes were available, the cap is 24, and the diagram was never rendered because mmdc was missing.
5. Errors: "module mermaid diagrams are capped at 24 nodes" and "mermaid renderer mmdc is not installed".

Verdict: PASS at full scope. The loans module is covered.

Two follow-ups: install mmdc on the demo rig, and persist graph.json instead of /tmp.

The five numbers are the scale slide of the talk, and docs/demo-pipeline.html carries the same gate result next to the pipeline diagram.
