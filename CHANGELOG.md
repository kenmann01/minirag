# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog, and this project adheres to
Semantic Versioning.

## Unreleased

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
