Onboarding cache: ARCHITECTURE.md, ENTRYPOINTS.md, CONFIG.md, GOTCHAS.md.

## Repository boundary

This repository keeps retrieval, generation, the corpus and the candidate-v1
endpoint (`python -m app candidate`). The evaluation harness, scoring,
graph materialisation, dashboards and the repository scanner live in
kenmann01/eval-harness; see `docs/MIGRATION.md`. `tests/test_no_harness.py`
fails if the harness comes back into `app/`.

## Agent skills

### Issue tracker

GitHub Issues on kenmann01/minirag, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical role names equal the GitHub label strings: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `docs/prd.md` (domain onboarding) and `docs/decision-log.md` (decision records) in `docs/`. See `docs/agents/domain.md`.
