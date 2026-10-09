# Demo rig

How to start, seed, replay, and tear down the local demo rig for The Map Writes the Test.
The rig is local-first (NFR-7): Docker Desktop runs postgres+pgvector and Grafana only,
Ollama runs as a native host service. Everything below was exercised end to end on the
demo machine; a cold start from wiped volumes to a green smoke run measured 28 seconds
with images and model weights already pulled.

## Start checklist

Run these before the room fills. Steps 1 and 2 are host apps, step 3 onward is the rig.

1. Start Docker Desktop and wait for the whale to settle.
2. Start Ollama from its native host app. Required for real runs only: the synthetic
   seed below uses fakes and never calls the model, so for a dry rehearsal this step
   can be skipped.
3. Bring the stack up and wait for postgres to accept connections:

   ```bash
   docker compose up -d
   docker compose exec -T postgres pg_isready -U minirag -d minirag
   ```

   `pg_isready` prints "accepting connections" when postgres is healthy. The first
   boot on wiped volumes runs `sql/001_init.sql` and takes the longest.

4. Cold-start verification. The script checks the merged compose config, the Grafana
   health endpoint, and one representative query per panel family:

   ```bash
   python scripts/smoke_rig.py
   ```

   Expected output (three PASS lines, then the summary, exit code 0):

   ```
   PASS step 1: docker compose config
   PASS step 2: grafana /api/health
   PASS step 3: panel family queries (a-e)
   smoke_rig: all steps passed against 127.0.0.1:5433/minirag
   ```

5. Open the board at http://127.0.0.1:3000/d/map-writes-the-test?kiosk
   (anonymous admin is enabled for the demo; the datasource picker must show
   minirag-postgres). After step 6 the panels fill on their own; the board
   refreshes every 5 seconds. The time picker is hidden because the panels
   follow the latest run, not the dashboard clock.

Order note: run `ossie validate` only after the seed (next section) has created the
metrics tables. On a wiped rig the metrics tables do not exist yet and validate
correctly reports the missing tables.

## Seed recipe

One full synthetic pass: real corpus, real Postgres, real Grafana, fake models.
This is the dry rehearsal. It needs no Ollama and no mapped repository on disk.

```bash
# 1. Ingest the Policy corpus (101 chunks on the current corpus).
python -m app ingest

# 2. Materialize the committed fixture graph into graph_nodes and graph_edges.
python -m app ossie materialize --graph eval/demo-graph.json

# 3. One gate row through the real writer, from the recorded gate document.
python -c "import json; from pathlib import Path; from app.metrics import MetricsSink; from app.pgadapter import PgAdapter; print(MetricsSink(PgAdapter()).record_gate(json.loads(Path('eval/fineract-gate.json').read_text())))"

# 4. One fake-model score run through the score CLI (dry rehearsal).
#    scratch/seed_rig.py (machine-local, not committed) monkeypatches the judge,
#    the agent, and the mapper exactly like tests/test_score_metrics.py, then
#    calls app.cli.main() with a real PgAdapter. Reports land in scratch/seed-reports.
python scratch/seed_rig.py

# 5. Now the contract is true: every declared table exists with its columns.
python -m app ossie validate   # exit 0, silent on success
```

After step 4 the board shows the synthetic run: the Results takeaway and pass
rate, tasks passed by tier, tool-call and cost totals, tier 2 grounding
distances, the per-task record, and the gate tiles (21.70 s, 61983 nodes,
303539 edges from the recorded gate).

Fixture graph: `eval/demo-graph.json` is a committed, realistic Fineract-shaped
graph in the graphify format (nodes with id, label, source_file; links with source,
target, relation, confidence). It carries exactly the four loan-first EXTRACTED call
edges the task generator selects, one non-loan edge, and one INFERRED edge the
generator must ignore. The same file feeds the score run, so board and tables come
from one graph.

## Real-run replay path

Same shape as the seed, but nothing is faked. Prerequisites:

- Ollama is running on the host and `OLLAMA_HOST` from `.env` answers:

  ```bash
  curl -s http://127.0.0.1:11434/api/tags
  ```

  `qwen3:8b` must appear in the models list. If it does not: `ollama pull qwen3:8b`.
- The mapped repository is checked out and the real graph.json is persisted on disk
  (not /tmp). The gate command produces both and records the graph path:

  ```bash
  python -m app gate --repo /path/to/fineract --output eval/fineract-gate.json
  ```

  The `graph_path` field of that JSON is the graph to score against. This run also
  appends one fresh gate_metrics row through the real mapper.

Then the real score run. No fakes: the judge and the agent both go through local
Ollama, temperature 0, seed 77:

```bash
python -m app score --repo /path/to/fineract --graph /path/to/graph.json --output eval/runs
```

Expected sink effects:

- one `runs` row per context arm (bare, map, map_rules) under one run id;
- one `task_results` row per scored case (8 tasks per arm, 24 rows per run),
  with grounding distances for tier 2;
- one `tool_calls` row per case, reconciled with the runs row;
- the board panels update on their next 5-second refresh.

Spot-check the sink with the board or directly:

```bash
docker compose exec postgres psql -U minirag -d minirag -c "SELECT context, tasks_total, tasks_passed FROM runs ORDER BY created_at DESC"
```

## Teardown

```bash
docker compose down        # keep volumes: runs, gate history, and board state survive
docker compose down -v     # full wipe: postgres_data and grafana_data are destroyed
```

`down` stops the containers and keeps both named volumes, so the seeded run stays on
the board for the next session. `down -v` returns the rig to factory state; the next
`docker compose up -d` is a cold start, and the seed recipe must run again before the
board shows anything.
