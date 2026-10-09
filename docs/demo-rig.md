# Demo rig

How to start, seed, replay, and tear down the local demo rig for The Map Writes
the Test. The rig is local-first (NFR-7): Docker Desktop runs postgres+pgvector
and Grafana only, Ollama runs as a native host service.

## Start checklist

Run these before the room fills. Steps 1 and 2 are host apps, step 3 onward is
the rig.

1. Start Docker Desktop and wait for the whale to settle.
2. Start Ollama from its native host app. Required for real runs only: the
   seeded runs use stand-in agents and judges and never call a model, so a dry
   rehearsal can skip this step.
3. Bring the stack up and wait for postgres to accept connections:

   ```bash
   docker compose up -d
   docker compose exec -T postgres pg_isready -U minirag -d minirag
   ```

   On this machine the gitignored `docker-compose.override.yml` publishes
   Postgres on 5433 (the host owns 5432) and `.env` points `DATABASE_URL`
   there. Fresh clones without the conflict use 5432.

4. Cold-start verification. The script checks the merged compose config, the
   Grafana health endpoint, and one representative query per board section:

   ```bash
   python scripts/smoke_rig.py
   ```

   Expected output (three PASS lines, then the summary, exit code 0):

   ```
   PASS step 1: docker compose config
   PASS step 2: grafana /api/health
   PASS step 3: board section queries
   smoke_rig: all steps passed against 127.0.0.1:5433/minirag
   ```

5. Open the board at http://127.0.0.1:3000/d/map-writes-the-test?kiosk
   (anonymous admin is enabled for the demo; the datasource picker must show
   minirag-postgres). The Run picker defaults to the latest stored run; every
   5-second refresh picks up new rows. The time picker is hidden because the
   single-run panels follow the run variable, not the dashboard clock.

Order note: run `ossie validate` only after the seed (next section) has created
the metrics tables. On a wiped rig the metrics tables do not exist yet and
validate correctly reports the missing tables.

## Seed recipe

One full synthetic pass: real corpus, real retrieval, real Postgres, real
Grafana, stand-in agents and judges. This is the dry rehearsal. It needs no
Ollama.

```bash
# 1. Ingest the banking corpus (131 chunks over 6 sections of 12 CFR 1026).
python -m app ingest

# 2. Materialize the committed demo graph into graph_nodes and graph_edges.
python -m app ossie materialize --graph eval/demo-graph.json

# 3. Five labeled seeded runs plus three real gate runs on the committed
#    demo tree. Runs are stored with origin='seeded' and backdated a few
#    hours so the trend lines spread across the board's six-hour window.
python scripts/seed_runs.py

# 4. Now the contract is true: every declared table exists with its columns.
python -m app ossie validate   # exit 0, silent on success
```

After step 3 the board shows the seeded runs: the takeaway sentence, the KPI
strip, run context (model, corpus, graph, task-list hash, origin), the
across-runs trends including the latest run, the change-vs-previous-run table,
the per-task matrix, grounding distances, and the gate row (about 30 nodes, 21
edges, ~0.08 s from the committed demo tree; the exact numbers are in
`eval/fineract-gate.json`).

Seeded rows are presentation only. Their answers come from stand-in agents and
judges, and their provenance says `seeded`; never quote them as model results.

## Real-run replay path

Same shape as the seed, but nothing is faked. Prerequisites:

- Ollama is running on the host and `OLLAMA_HOST` from `.env` answers:

  ```bash
  curl -s http://127.0.0.1:11434/api/tags
  ```

  `qwen3:8b` must appear in the models list. If it does not: `ollama pull
  qwen3:8b`.
- The mapped repository is checked out and the real graph.json is persisted on
  disk (not /tmp). The gate command produces both and records the graph path:

  ```bash
  python -m app gate --repo /path/to/fineract --output eval/fineract-gate.json
  ```

  The `graph_path` field of that JSON is the graph to score against. This run
  also appends one fresh gate_metrics row through the real mapper.

Then the real score run. No fakes: the judge and the agent both go through
local Ollama, temperature 0, seed 77 (or set `JUDGE_PROVIDER=bedrock` to grade
with Bedrock instead):

```bash
python -m app score --repo /path/to/fineract --graph /path/to/graph.json \
    --output eval/runs --label "real run"
```

Expected sink effects:

- one `runs` row per context arm (bare, map, map_rules) under one run id,
  stamped with label, model, corpus, graph, task-list hash, and origin=real;
- one `task_results` row per scored case (8 tasks per arm, 24 rows per run),
  with grounding distances for tier 2;
- one `tool_calls` row per case, reconciled with the runs row;
- the board panels update on their next 5-second refresh.

Spot-check the sink with the board or directly:

```bash
docker compose exec postgres psql -U minirag -d minirag -c "SELECT label, context, tasks_passed FROM runs ORDER BY created_at DESC"
```

## Board upkeep

Edit `scripts/build_dashboard.py`, then rerun it; never hand-edit
`grafana/dashboards/map-writes-the-test.json` (`tests/test_rig_files.py`
rebuilds the JSON from the builder and fails on drift). Grafana reloads the
provisioned board within about 10 seconds. Panels below the fold render
lazily; scroll once before screenshotting a cold board.

## Teardown

```bash
docker compose down        # keep volumes: runs, gate history, and board state survive
docker compose down -v     # full wipe: postgres_data and grafana_data are destroyed
```

`down` stops the containers and keeps both named volumes, so the seeded runs
stay on the board for the next session. `down -v` returns the rig to factory
state; the next `docker compose up -d` is a cold start, and the seed recipe
must run again before the board shows anything.
