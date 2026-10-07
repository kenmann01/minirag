# Internal and Confidential - Not for External Distribution.
"""Persist scored-run and gate metrics into Postgres through the adapter."""

import uuid

from psycopg.types.json import Json

from app.db import DatabaseAdapter

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    run_id UUID NOT NULL,
    context TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tasks_total INT NOT NULL,
    tasks_passed INT NOT NULL,
    passed_tier1 INT NOT NULL,
    passed_tier2 INT NOT NULL,
    tool_calls INT NOT NULL,
    cost_usd DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, context)
)
"""

_CREATE_TASK_RESULTS = """
CREATE TABLE IF NOT EXISTS task_results (
    run_id UUID NOT NULL,
    context TEXT NOT NULL,
    task_id TEXT NOT NULL,
    tier INT NOT NULL,
    passed BOOLEAN NOT NULL,
    origin JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, context, task_id)
)
"""

_CREATE_TOOL_CALLS = """
CREATE TABLE IF NOT EXISTS tool_calls (
    run_id UUID NOT NULL,
    context TEXT NOT NULL,
    task_id TEXT NOT NULL,
    tool_calls INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, context, task_id)
)
"""

_CREATE_GATE_METRICS = """
CREATE TABLE IF NOT EXISTS gate_metrics (
    run_id UUID NOT NULL PRIMARY KEY,
    gate JSONB NOT NULL,
    wall_time_seconds DOUBLE PRECISION NOT NULL,
    node_count BIGINT NOT NULL,
    edge_count BIGINT NOT NULL,
    passed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_INSERT_RUN = """
INSERT INTO runs (
    run_id, context, tasks_total, tasks_passed,
    passed_tier1, passed_tier2, tool_calls, cost_usd
)
VALUES (
    %(run_id)s, %(context)s, %(tasks_total)s, %(tasks_passed)s,
    %(passed_tier1)s, %(passed_tier2)s, %(tool_calls)s, %(cost_usd)s
)
"""


_INSERT_TASK_RESULT = """
INSERT INTO task_results (run_id, context, task_id, tier, passed, origin)
VALUES (%(run_id)s, %(context)s, %(task_id)s, %(tier)s, %(passed)s, %(origin)s)
"""

_INSERT_TOOL_CALL = """
INSERT INTO tool_calls (run_id, context, task_id, tool_calls)
VALUES (%(run_id)s, %(context)s, %(task_id)s, %(tool_calls)s)
"""

_INSERT_GATE_METRIC = """
INSERT INTO gate_metrics (
    run_id, gate, wall_time_seconds, node_count, edge_count, passed
)
VALUES (
    %(run_id)s, %(gate)s, %(wall_time_seconds)s, %(node_count)s,
    %(edge_count)s, %(passed)s
)
"""

_BACKFILLABLE_COLUMNS = {
    "runs": (
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("tasks_total", "INT NOT NULL DEFAULT 0"),
        ("tasks_passed", "INT NOT NULL DEFAULT 0"),
        ("passed_tier1", "INT NOT NULL DEFAULT 0"),
        ("passed_tier2", "INT NOT NULL DEFAULT 0"),
        ("tool_calls", "INT NOT NULL DEFAULT 0"),
        ("cost_usd", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
    ),
    "task_results": (
        ("tier", "INT NOT NULL DEFAULT 0"),
        ("passed", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("origin", "JSONB"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    "tool_calls": (
        ("tool_calls", "INT NOT NULL DEFAULT 0"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    "gate_metrics": (
        ("gate", "JSONB"),
        ("wall_time_seconds", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
        ("node_count", "BIGINT NOT NULL DEFAULT 0"),
        ("edge_count", "BIGINT NOT NULL DEFAULT 0"),
        ("passed", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
}


def _ensure_columns(conn, table: str) -> None:
    """Add any missing metric column back onto an older table."""
    found = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        ).fetchall()
    }
    for column, ddl in _BACKFILLABLE_COLUMNS[table]:
        if column not in found:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")


def _ensure_schema(conn) -> None:
    """Create the metrics tables when missing and heal older schemas."""
    conn.execute(_CREATE_RUNS)
    conn.execute(_CREATE_TASK_RESULTS)
    conn.execute(_CREATE_TOOL_CALLS)
    conn.execute(_CREATE_GATE_METRICS)
    for table in ("runs", "task_results", "tool_calls", "gate_metrics"):
        _ensure_columns(conn, table)


class MetricsSink:
    """Write scored-run and gate metrics, one row per run or context arm."""

    def __init__(self, adapter: DatabaseAdapter):
        self._adapter = adapter
        self._ensured = False

    def record_context(self, *, run_id, context: str, scoreboard: dict, cases: list[dict]) -> None:
        """Store one context arm's scoreboard summary and case rows.

        Args:
            run_id: Run identity shared by every context arm of one invocation.
            context: Context arm name: bare, map, or map_rules.
            scoreboard: The scoreboard row written for this context arm.
            cases: One row per case with task_id, tier, passed, origin, and tool_calls.
        """
        with self._adapter.connect() as conn:
            if not self._ensured:
                _ensure_schema(conn)
                self._ensured = True
            conn.execute(
                _INSERT_RUN,
                {
                    "run_id": run_id,
                    "context": context,
                    "tasks_total": scoreboard["tasks_total"],
                    "tasks_passed": scoreboard["tasks_passed"],
                    "passed_tier1": scoreboard["passed_by_tier"]["1"]["passed"],
                    "passed_tier2": scoreboard["passed_by_tier"]["2"]["passed"],
                    "tool_calls": scoreboard["tool_calls"],
                    "cost_usd": scoreboard["cost_usd"],
                },
            )
            for case in cases:
                origin = case.get("origin")
                conn.execute(
                    _INSERT_TASK_RESULT,
                    {
                        "run_id": run_id,
                        "context": context,
                        "task_id": case["task_id"],
                        "tier": case["tier"],
                        "passed": case["passed"],
                        "origin": Json(origin) if origin is not None else None,
                    },
                )
                conn.execute(
                    _INSERT_TOOL_CALL,
                    {
                        "run_id": run_id,
                        "context": context,
                        "task_id": case["task_id"],
                        "tool_calls": case["tool_calls"],
                    },
                )

    def record_gate(self, gate: dict) -> uuid.UUID:
        """Store one gate run's numbers as a new history row.

        Args:
            gate: The gate document written to the gate JSON output.

        Returns:
            The fresh run identity stored as the new row's primary key.

        Side Effects:
            Appends exactly one gate_metrics row per call. Older rows are
            never mutated, so re-running the gate accumulates history.
        """
        run_id = uuid.uuid4()
        with self._adapter.connect() as conn:
            if not self._ensured:
                _ensure_schema(conn)
                self._ensured = True
            conn.execute(
                _INSERT_GATE_METRIC,
                {
                    "run_id": run_id,
                    "gate": Json(gate),
                    "wall_time_seconds": gate["wall_time_seconds"],
                    "node_count": gate["node_count"],
                    "edge_count": gate["edge_count"],
                    "passed": gate["node_count"] > 0,
                },
            )
        return run_id
