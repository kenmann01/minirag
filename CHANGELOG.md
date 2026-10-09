# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog, and this project adheres to
Semantic Versioning.

## Unreleased

### Changed (2026-10-09 audit)

- Evaluation honesty: the map excerpt no longer prints the graded target, the
  agent is no longer told to copy the prompt, the live trace reports what
  retrieval actually returned, the tier-2 judge now sees the reference chunk
  text (one evidence-based citation check replaces the blind LLMJudge), and
  the tier-1 grader requires both symbol and file.
- Pluggable judge: JUDGE_PROVIDER=ollama|bedrock with a Bedrock converse
  adapter (credentials from the standard AWS chain, explicit JUDGE_MODEL,
  boto3 optional) in app/generation/judge.py.
- Run provenance: runs carry label, model, corpus, graph, task-list hash, and
  origin (real|seeded); gate rows carry a repo label; the OSSIE map declares
  the new columns.
- Board rebuilt from code: scripts/build_dashboard.py generates the Grafana
  dashboard (KPI strip, run picker, across-runs trends including the latest
  run, change-vs-previous-run table, per-task matrix, gate history);
  test_rig_files and smoke_rig lock it.
- Package restructure: app/ split into corpus, retrieval, generation, harness,
  storage, observability, and web subpackages; the answer prompt moved under
  app/generation/prompts; convert_docs moved into scripts/.
- Corpus migration: default corpus is corpora/banking (recursive ingest,
  embedding model stamped per chunk, search refuses foreign vectors), and a
  fresh ten-golden exam authored from the Regulation Z slice.
- Removed: ask/serve/cache/trace old-RAG surface, build_submission.py, the
  Minion corpus references, and the destructive local-DB test behavior (the
  suite now runs against an auto-created minirag_test database).
- Gate honesty: loans coverage uses the real graph check, the unconditional
  fake error is gone, dead render ceiling removed, and record_gate failures
  warn instead of crashing after the file is written.
- Seeded rig: scripts/seed_runs.py writes labeled seeded runs and real gate
  history on the committed demo tree (eval/demo-repo); the committed gate
  record was regenerated from it.


Workstream of 2026-10-07.

### Added

- Scored-run metrics sink that persists per-task results for later analysis (#31).
- Gate numbers now flow into the metrics sink (#33).
- OSSIE materialization: repository graphs load into Postgres and the declared map validates live (#32).
- Grafana board with provisioning as code for the metrics sink (#34).
- Demo rig board seeded and PRD statuses flipped (#35).

### Changed

- Hygiene pass: untracked build artifacts, ignored local files, removed orphaned files (#37).
- The test suite environment is pinned to CI truth (#38).
- Docstrings on every public definition, locked in by an AST regression test (#39).
- Packaging: pyproject with project metadata and lint configuration, requirements split into runtime and development files, huggingface_hub declared as a direct dependency, version constant 0.3.0, and ruff and black clean across the package (#40).
