# The RAG candidate (candidate-v1)

The eval harness lives in its own repository. This repository keeps retrieval
and generation, and `app/candidate.py` exposes them behind the harness's
candidate-v1 HTTP contract. The candidate never scores itself and never reads a
spec: the harness owns cases, metrics, prompt variants, context, and prices.

## The contract and the vendoring rule

The harness owns `contract/candidate-v1.schema.json`. This repository vendors
a verbatim copy at `contract/candidate-v1.schema.json` and treats it as
read-only:

- The harness is the source of truth; never edit the vendored file by hand.
- Re-vendor by copying the harness file over the local one and updating
  `PINNED_SHA256` in `tests/test_candidate_contract.py`.
- `tests/test_candidate_contract.py` always checks the pinned sha. When a
  sibling harness clone is present (or `EVAL_HARNESS_ROOT` points at one), it
  also compares the bytes and fails on any drift.

Version 1 is append-compatible only: optional request and response fields may
be added, but no v1 field is removed, renamed, or retyped. The harness ignores
response fields it does not need.

## Request to response mapping

`run_candidate(request, *, deps=None) -> dict` is pure. It validates the v1
request, runs one RAG attempt, and returns the v1 response body.

| Request field | Where it goes |
| --- | --- |
| `case_id` | Copied to `artifacts.case_id` so a cell trace can name the case. |
| `prompt` | The question passed to retrieval and generation. |
| `context` | When non-empty, these chunks are the evidence and retrieval is skipped. Each chunk is normalised to the generator's record shape (missing `source_doc`/`section` are derived from the chunk id). When empty, the candidate calls the repository's retriever. |
| `prompt_override` | Passed to `generate(..., prompt_text=...)` as the prompt template. It accepts the same `{excerpts}` and `{question}` placeholders as `app/generation/prompts/policy_answer_v3.md`. `null` keeps the committed v3 prompt. |
| `seed` | Passed through `generate(..., seed=...)` to the model adapter. The adapter default stays 77, so a request that omits the seed behaves like today. |

| Response field | Where it comes from |
| --- | --- |
| `answer` | `AskResponse.answer` from `app.generation.generate`. |
| `citations` | The generator's `Citation.section` mapped back to the matching chunk id. A refusal returns `[]`. |
| `usage.prompt_tokens` / `usage.completion_tokens` | Provider counts on `AskResponse`. The Ollama adapter now returns a `ChatReply` (a `str` subclass) carrying `prompt_eval_count` and `eval_count`. An empty retrieval refuses before the model call, so both counts are 0. |
| `tool_calls` | 1 when the candidate called the retriever, 0 when the harness injected context. Retrieval is the candidate's only tool. |
| `artifacts` | `case_id`, `retrieval` (`request` or `retrieval`), `chunk_ids`, `seed`, `prompt_source` (`override` or `default`), and `model` when the adapter exposes a name. |

The candidate does not emit the optional top-level `model` field; the model
name is in `artifacts`, and the harness falls back to the spec's candidate
model as the contract allows.

## The knobs, and the defaults that did not move

- `generate(question, chunks, model, *, prompt_text=None, seed=None)` uses a
  prompt variant when given. Existing callers pass no seed, so the adapter's
  own default is used.
- `OllamaAdapter.chat(prompt, *, response_format="json", seed=77)` still sends
  `temperature: 0` and `seed: 77` by default. A request seed overrides only the
  seed; the temperature stays at 0.
- A request context replaces retrieval; a request without context uses
  `app.retrieval.bridge.retrieve`, which is the same hybrid search the eval
  pipeline already uses.
- `python -m app score` is unchanged: it never calls the candidate, and the
  adapter defaults are the same as before.

## Transport

Two modes, one code path. Both call `run_candidate`.

```bash
# Serve POST /run and GET /health with the standard-library http.server.
python -m app candidate --serve --port 8768

# One request file, one response file.
python -m app candidate --request request.json --response response.json
```

The endpoint is local and unauthenticated by design; point the harness at it
with `auth: none`, or put it behind a proxy that holds the credential. A
request you can paste into a terminal:

```bash
curl -s http://127.0.0.1:8768/run \
  -H 'Content-Type: application/json' \
  -d '{
    "case_id": "parity-t2-01",
    "prompt": "What rule does Loan1 state in bank-1?",
    "context": [{"chunk_id": "bank-1", "text": "Rule bank-1 states the fixture obligation for Loan1."}],
    "prompt_override": null,
    "seed": 77
  }'
```

A bad body is a 400 with a JSON `error`. A failure inside retrieval or
generation is a 500, which the harness treats as a retryable transport failure;
a wrong answer is still a 200 and is scored, never retried.

## The fixture and the parity test

`eval/fixtures/parity/` is deterministic and was generated from fakes, not a
live model:

- `graph.json`: the four-loan graph the task generator reads.
- `tasks.json`: four tier-1 and four tier-2 tasks, in the `Task` shape.
- `retrieval.json`: the canned bridge payload for each tier-2 prompt.
- `answers.json`: the canned answer table, token usage, judge usage, seed, and
  model name.
- `expected.json`: the per-model price table and the expected scoreboard.
- `harness-spec.yaml`: the demo spec in the next section.

The three arms are the existing `run_score` arms: `bare` (`no map, no rules`),
`map`, and `map_rules`. The fixture records:

| Arm | Tasks | Tier 1 | Tier 2 | Tool calls | Prompt tokens | Completion tokens | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bare | 4/8 | 0/4 | 4/4 | 8 | 726 | 118 | 0.0081797 |
| map | 4/8 | 4/4 | 0/4 | 0 | 1212 | 204 | 0.0003042 |
| map_rules | 8/8 | 4/4 | 4/4 | 0 | 1212 | 204 | 0.0003042 |

`tests/test_candidate.py` runs the same cases twice through `run_score`:

1. The **current path**: a `task_fn` that reads the fixture table directly.
2. The **spec path**: a `task_fn` that builds a v1 request for every turn and
   calls `run_candidate`.

`run_score` gained one small seam, `tasks=None`. When it is given a list it
loads the graph for the map excerpts and skips task generation, so both paths
score the identical committed task list. The parity assertions are:

- the same task totals and the same pass count for every tier, cell for cell;
- the same tool-call count;
- the same prompt and completion totals;
- cost within one cent of the committed price-table figure;
- the committed `expected.json` scoreboard unchanged, so any drift fails.

Costs come from the spec price table, not one global triple. `expected.json`
holds a table keyed by model and the parity test recomputes every row's cost
from that row and the observed tokens. The candidate itself only reports model
names and provider usage; pricing belongs to the harness spec, exactly as the
design note requires. `harness-spec.yaml` carries the same `input`/`output`
shape under `prices:`.

The test needs no Postgres and no Ollama: it injects `database_adapter=None`
and the fake retriever, generator, and judge. Only a test that wires the real
modules (the `DefaultCandidateDeps` wiring test) needs the installed RAG stack;
it runs in CI.

## Two-command demo against the harness HTTP candidate

Terminal 1, in this repository:

```bash
python -m app candidate --serve --port 8768
```

Terminal 2, in the harness clone (with its dependencies installed):

```bash
python -m evalharness run /path/to/minirag/eval/fixtures/parity/harness-spec.yaml
```

The spec points at `http://127.0.0.1:8768`, declares `auth: none`, injects the
rule chunk, and uses the `contains` metric. It needs the configured Ollama
model, but no Postgres, because the context is injected. The same endpoint can
be exercised with the harness conformance suite:

```bash
python - <<'PY'
from evalharness.conformance import run_suite
report = run_suite(
    "evalharness.adapters.candidates.http:build",
    "candidate",
    options={"base_url": "http://127.0.0.1:8768", "auth": "none"},
)
assert report.passed, [(r.clause, r.detail) for r in report.results if not r.ok]
print("candidate/1..8 passed")
PY
```

The `--adapter` form of the command does not read the manifest's `base_url`,
so pass the endpoint through `run_suite(options=...)` as above. This repository
was checked against the eight `candidate/*` clauses with that call.

## What the split follow-up can delete

`app/candidate.py` does not import `app.harness`; a test enforces that. The
parity test is the only new code allowed to import `run_score`. When issue
#16 deletes the harness from this repository, `app/candidate.py`, the
`candidate` subcommand, the vendored contract, and the fixture can stay; the
`tasks=` seam in `app/harness/score.py` and the `tests/test_candidate.py`
parity tests go with the harness.
