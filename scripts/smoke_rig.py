"""Preflight the local demo rig: compose config, Grafana health, and panel SQL."""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
GRAFANA_HEALTH_URL = "http://127.0.0.1:3000/api/health"
HEALTH_RETRIES = 15
HEALTH_RETRY_SECONDS = 2.0

# One representative query per dashboard panel family (a through e).
# Keep each string identical to the rawSql of its panel in
# grafana/dashboards/map-writes-the-test.json.
FAMILIES = (
    (
        "a",
        "tasks passed by tier per arm",
        "SELECT context AS arm, tasks_total, passed_tier1, passed_tier2 "
        "FROM runs "
        "WHERE run_id = (SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1) "
        "ORDER BY context",
    ),
    (
        "b",
        "tool calls per arm",
        "SELECT created_at AS time, context AS arm, tool_calls "
        "FROM runs "
        "WHERE run_id = (SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1) "
        "ORDER BY created_at",
    ),
    (
        "c",
        "cost per run",
        "SELECT created_at AS time, context AS arm, cost_usd "
        "FROM runs "
        "WHERE run_id = (SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1) "
        "ORDER BY created_at",
    ),
    (
        "d",
        "grounding distance distribution",
        "SELECT context AS arm, task_id, grounding_distance "
        "FROM task_results "
        "WHERE tier = 2 AND grounding_distance IS NOT NULL "
        "AND run_id = (SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1) "
        "ORDER BY created_at DESC, task_id",
    ),
    (
        "e",
        "gate stats",
        "SELECT wall_time_seconds "
        "FROM gate_metrics "
        "ORDER BY created_at DESC LIMIT 1",
    ),
)


def database_url() -> str:
    """Read the sink URL from the environment or the local .env file."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL is not set and .env has no DATABASE_URL line")


def check_compose_config() -> None:
    """Step 1: the merged compose configuration must validate."""
    done = subprocess.run(
        ["docker", "compose", "config", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise RuntimeError(f"docker compose config -q failed: {done.stderr.strip()}")


def check_grafana_health() -> None:
    """Step 2: the Grafana /api/health endpoint must answer HTTP 200."""
    last_error: Exception | None = None
    for _ in range(HEALTH_RETRIES):
        try:
            with urllib.request.urlopen(GRAFANA_HEALTH_URL, timeout=5) as response:
                if response.status == 200:
                    return
                raise RuntimeError(f"Grafana /api/health returned HTTP {response.status}")
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            time.sleep(HEALTH_RETRY_SECONDS)
    raise RuntimeError(f"Grafana /api/health never answered 200: {last_error}")


def check_panel_queries() -> None:
    """Step 3: one representative query per panel family must run on the sink."""
    url = database_url()
    with psycopg.connect(url) as conn:
        for letter, label, query in FAMILIES:
            try:
                conn.execute(query).fetchall()
            except psycopg.Error as exc:
                raise RuntimeError(
                    f"panel family {letter} ({label}) query failed: {exc}"
                ) from exc


def main() -> int:
    """Run every preflight step, print PASS per step, exit 0 or 1."""
    steps = (
        ("1: docker compose config", check_compose_config),
        ("2: grafana /api/health", check_grafana_health),
        (
            "3: panel family queries (a-e)",
            check_panel_queries,
        ),
    )
    for name, check in steps:
        try:
            check()
        except Exception as exc:
            print(f"FAIL step {name}: {exc}", file=sys.stderr)
            return 1
        print(f"PASS step {name}")
    info = psycopg.conninfo.conninfo_to_dict(database_url())
    print(
        "smoke_rig: all steps passed against "
        f"{info.get('host')}:{info.get('port')}/{info.get('dbname')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
