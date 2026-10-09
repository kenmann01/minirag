#!/usr/bin/env python
"""Build grafana/dashboards/map-writes-the-test.json deterministically.

Run from the repo root after changing the board layout or queries:

    python scripts/build_dashboard.py

The board reads the Postgres metrics store directly (runs, task_results,
tool_calls, gate_metrics). Panels that describe one run follow the ``run``
template variable (latest by default); trend, delta, and gate panels read
full history so the latest run is never excluded.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "grafana" / "dashboards" / "map-writes-the-test.json"

DS = {"type": "grafana-postgresql-datasource", "uid": "${datasource}"}

ARMS = (
    ("bare", "Baseline (no map)", "#8F9BB3"),
    ("map", "Map only", "#3FB5A5"),
    ("map_rules", "Map + rules", "#5B8DEF"),
)

ARM_CASE = (
    "CASE r.context "
    + " ".join(f"WHEN '{key}' THEN '{label}'" for key, label, _ in ARMS)
    + " ELSE r.context END"
)

LEADERS_ARM_CASE = (
    "CASE leaders.context "
    + " ".join(f"WHEN '{key}' THEN '{label}'" for key, label, _ in ARMS)
    + " ELSE leaders.context END"
)

AGG_ARM_CASE = (
    "CASE agg.context "
    + " ".join(f"WHEN '{key}' THEN '{label}'" for key, label, _ in ARMS)
    + " ELSE agg.context END"
)

ARM_OVERRIDES = [
    {
        "matcher": {"id": "byName", "options": label},
        "properties": [
            {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
            {"id": "custom.lineWidth", "value": 2},
            {"id": "showPoints", "value": "always"},
        ],
    }
    for _, label, color in ARMS
]

RUN_FILTER = "r.run_id::text = '${run}'"


def target(sql: str, fmt: str = "table") -> dict:
    """One query target bound to the board's datasource."""
    return {"datasource": DS, "editorMode": "code", "format": fmt, "rawSql": sql, "refId": "A"}


def panel(pid: int, title: str, ptype: str, x: int, y: int, w: int, h: int, **extra) -> dict:
    """Assemble one panel skeleton with grid position and datasource."""
    body = {
        "id": pid,
        "title": title,
        "type": ptype,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
    }
    body.update(extra)
    return body


def header(pid: int, title: str, body: str, y: int) -> dict:
    """A full-width markdown section header; the title bar names it."""
    # The panel title bar already carries the section name; the markdown body
    # must not repeat it or the section header renders twice.
    return panel(
        pid,
        title,
        "text",
        0,
        y,
        24,
        2,
        options={
            "content": body,
            "mode": "markdown",
        },
        fieldConfig={"defaults": {}, "overrides": []},
        targets=[],
    )


def arm_pivot(select_inner: str, agg: str = "MAX") -> str:
    """One aggregated column per arm, each carrying its display name."""
    return ", ".join(
        f"{agg}(CASE WHEN r.context = '{key}' THEN {select_inner} END) AS \"{label}\""
        for key, label, _ in ARMS
    )


# ---------------------------------------------------------------- queries

TAKEAWAY_SQL = (
    "WITH latest AS ( "
    "SELECT context, tasks_total, tasks_passed, passed_tier1, passed_tier2 "
    f"FROM runs r WHERE {RUN_FILTER} "
    "), tier_n AS ( "
    "SELECT tier, COUNT(DISTINCT task_id)::int AS n FROM task_results t "
    f"WHERE t.run_id::text = '${{run}}' GROUP BY tier "
    "), b AS (SELECT * FROM latest WHERE context = 'bare'), "
    "m AS (SELECT * FROM latest WHERE context = 'map'), "
    "r2 AS (SELECT * FROM latest WHERE context = 'map_rules') "
    "SELECT CASE "
    "WHEN (SELECT COUNT(*) FROM b) = 0 OR (SELECT COUNT(*) FROM m) = 0 "
    "OR (SELECT COUNT(*) FROM r2) = 0 THEN 'This run is missing one of the three arms.' "
    "WHEN (SELECT passed_tier1 FROM m) > (SELECT passed_tier1 FROM b) "
    "AND (SELECT passed_tier1 FROM r2) = (SELECT passed_tier1 FROM m) "
    "AND (SELECT passed_tier2 FROM m) = (SELECT passed_tier2 FROM b) "
    "AND (SELECT passed_tier2 FROM r2) = (SELECT passed_tier2 FROM b) "
    "THEN format('Map context lifts tier 1 from %s of %s to %s of %s and does not lift tier 2.', "
    "(SELECT passed_tier1 FROM b), COALESCE((SELECT n FROM tier_n WHERE tier = 1), 0), "
    "(SELECT passed_tier1 FROM m), COALESCE((SELECT n FROM tier_n WHERE tier = 1), 0)) "
    "WHEN (SELECT tasks_passed FROM m) > (SELECT tasks_passed FROM b) "
    "AND (SELECT tasks_passed FROM r2) > (SELECT tasks_passed FROM m) "
    "THEN format('Passes rise from %s to %s to %s of %s across baseline, map only, and map + rules.', "
    "(SELECT tasks_passed FROM b), (SELECT tasks_passed FROM m), "
    "(SELECT tasks_passed FROM r2), (SELECT tasks_total FROM b)) "
    "WHEN (SELECT tasks_passed FROM m) = (SELECT tasks_passed FROM r2) "
    "AND (SELECT tasks_passed FROM m) > (SELECT tasks_passed FROM b) "
    "THEN format('Map only and Map + rules tie at %s of %s. Baseline passed %s of %s.', "
    "(SELECT tasks_passed FROM m), (SELECT tasks_total FROM m), "
    "(SELECT tasks_passed FROM b), (SELECT tasks_total FROM b)) "
    "ELSE format('Baseline %s of %s, Map only %s of %s, Map + rules %s of %s. "
    "Tier 1 is %s, %s, and %s of %s. Tier 2 is %s, %s, and %s of %s.', "
    "(SELECT tasks_passed FROM b), (SELECT tasks_total FROM b), "
    "(SELECT tasks_passed FROM m), (SELECT tasks_total FROM m), "
    "(SELECT tasks_passed FROM r2), (SELECT tasks_total FROM r2), "
    "(SELECT passed_tier1 FROM b), (SELECT passed_tier1 FROM m), "
    "(SELECT passed_tier1 FROM r2), COALESCE((SELECT n FROM tier_n WHERE tier = 1), 0), "
    "(SELECT passed_tier2 FROM b), (SELECT passed_tier2 FROM m), "
    "(SELECT passed_tier2 FROM r2), COALESCE((SELECT n FROM tier_n WHERE tier = 2), 0)) "
    "END AS takeaway"
)

LEADING_ARM_SQL = (
    "WITH latest AS ( "
    "SELECT context, tasks_passed, tasks_total FROM runs r "
    f"WHERE {RUN_FILTER} "
    "), best AS (SELECT MAX(tasks_passed) AS top FROM latest), "
    "leaders AS (SELECT latest.context, latest.tasks_passed FROM latest, best "
    "WHERE latest.tasks_passed = best.top) "
    "SELECT CASE WHEN (SELECT COUNT(*) FROM leaders) = 1 THEN 'Best · ' || "
    f"{LEADERS_ARM_CASE} "
    "ELSE 'Tie' END "
    "|| ', ' || leaders.tasks_passed || ' of ' "
    "|| (SELECT tasks_total FROM latest LIMIT 1) || ' passed' AS summary "
    "FROM leaders"
)

PASS_RATE_SQL = (
    f"SELECT {arm_pivot('ROUND(100.0 * r.tasks_passed / NULLIF(r.tasks_total, 0), 1)::numeric')} "
    f"FROM runs r WHERE {RUN_FILTER}"
)

TOOL_CALLS_SQL = f"SELECT {arm_pivot('r.tool_calls::int')} FROM runs r WHERE {RUN_FILTER}"

COST_SQL = f"SELECT {arm_pivot('ROUND(r.cost_usd::numeric, 4)')} FROM runs r WHERE {RUN_FILTER}"

RUN_CONTEXT_SQL = (
    "SELECT COALESCE(r.label, '—') AS \"Label\", COALESCE(r.model, '—') AS \"Model\", "
    "COALESCE(r.corpus, '—') AS \"Corpus\", COALESCE(r.graph, '—') AS \"Graph\", "
    "COALESCE(r.task_list_sha, '—') AS \"Task list\", "
    "COALESCE(r.origin, 'real') AS \"Origin\", "
    "to_char(min(r.created_at), 'MM-DD HH24:MI') AS \"Started\" "
    f"FROM runs r WHERE {RUN_FILTER} "
    "GROUP BY r.run_id, r.label, r.model, r.corpus, r.graph, r.task_list_sha, r.origin"
)

TREND_SQL = {
    "pass": (
        "SELECT r.created_at AS time, "
        f"{ARM_CASE} AS metric, "
        "ROUND(100.0 * r.tasks_passed / NULLIF(r.tasks_total, 0), 1)::numeric AS value "
        "FROM runs r ORDER BY r.created_at ASC"
    ),
    "tools": (
        "SELECT r.created_at AS time, "
        f"{ARM_CASE} AS metric, "
        "r.tool_calls::int AS value FROM runs r ORDER BY r.created_at ASC"
    ),
    "cost": (
        "SELECT r.created_at AS time, "
        f"{ARM_CASE} AS metric, "
        "ROUND(r.cost_usd::numeric, 4) AS value FROM runs r ORDER BY r.created_at ASC"
    ),
}

DELTA_SQL = (
    "WITH first_seen AS ( "
    "SELECT DISTINCT ON (run_id) run_id, created_at FROM runs "
    "ORDER BY run_id, created_at ASC "
    "), ranked AS ( "
    "SELECT run_id, created_at, DENSE_RANK() OVER (ORDER BY created_at DESC) AS rk "
    "FROM first_seen "
    "), cur AS (SELECT run_id FROM ranked WHERE rk = 1), "
    "prv AS (SELECT run_id FROM ranked WHERE rk = 2), agg AS ( "
    "SELECT r.context, "
    "MAX(r.tasks_total)::int AS total, "
    "MAX(CASE WHEN r.run_id IN (SELECT run_id FROM cur) THEN r.tasks_passed END)::int AS p_now, "
    "MAX(CASE WHEN r.run_id IN (SELECT run_id FROM prv) THEN r.tasks_passed END)::int AS p_prev, "
    "MAX(CASE WHEN r.run_id IN (SELECT run_id FROM cur) THEN r.tool_calls END)::int AS t_now, "
    "MAX(CASE WHEN r.run_id IN (SELECT run_id FROM prv) THEN r.tool_calls END)::int AS t_prev, "
    "MAX(CASE WHEN r.run_id IN (SELECT run_id FROM cur) THEN r.cost_usd END) AS c_now, "
    "MAX(CASE WHEN r.run_id IN (SELECT run_id FROM prv) THEN r.cost_usd END) AS c_prev "
    "FROM runs r GROUP BY r.context "
    ") SELECT "
    f'{AGG_ARM_CASE} AS "Arm", '
    'ROUND(100.0 * (p_now - p_prev) / NULLIF(total, 0), 1)::numeric AS "Pass Δ (pp)", '
    '(t_now - t_prev)::int AS "Tool calls Δ", '
    'ROUND((c_now - c_prev)::numeric, 4) AS "Cost Δ (USD)" '
    "FROM agg ORDER BY 1"
)

TIER_SQL = (
    "SELECT 'Tier 1 · code facts' AS \"Tier\", "
    + arm_pivot("r.passed_tier1::int")
    + f" FROM runs r WHERE {RUN_FILTER} "
    "UNION ALL SELECT 'Tier 2 · cited rules', "
    + arm_pivot("r.passed_tier2::int")
    + f" FROM runs r WHERE {RUN_FILTER}"
)

MATRIX_SQL = (
    'SELECT t.task_id AS "Task", '
    + ", ".join(
        f"MAX(CASE WHEN t.context = '{key}' THEN CASE WHEN t.passed THEN 'pass' ELSE 'fail' END END) "
        f'AS "{label}"'
        for key, label, _ in ARMS
    )
    + " FROM task_results t WHERE t.run_id::text = '${run}' "
    "GROUP BY t.task_id ORDER BY t.task_id"
)

GROUNDING_SQL = (
    'SELECT t.task_id AS "Task", '
    + ", ".join(
        f"ROUND(MAX(CASE WHEN t.context = '{key}' THEN t.grounding_distance END)::numeric, 3) "
        f'AS "{label}"'
        for key, label, _ in ARMS
    )
    + " FROM task_results t WHERE t.run_id::text = '${run}' AND t.tier = 2 "
    "GROUP BY t.task_id ORDER BY t.task_id"
)

GATE_RESULT_SQL = (
    "SELECT CASE WHEN passed THEN 1 ELSE 0 END AS result "
    "FROM gate_metrics ORDER BY created_at DESC LIMIT 1"
)

GATE_TIME_SQL = (
    "SELECT created_at AS time, ROUND(wall_time_seconds::numeric, 3) AS value "
    "FROM gate_metrics ORDER BY created_at ASC"
)

GATE_NODES_SQL = (
    "SELECT created_at AS time, node_count::int AS value FROM gate_metrics ORDER BY created_at ASC"
)

GATE_EDGES_SQL = (
    "SELECT created_at AS time, edge_count::int AS value FROM gate_metrics ORDER BY created_at ASC"
)

GATE_HISTORY_SQL = (
    "SELECT to_char(created_at, 'YYYY-MM-DD HH24:MI') AS \"At\", "
    "COALESCE(label, '—') AS \"Repo\", "
    "CASE WHEN passed THEN 'Passed' ELSE 'Not passed' END AS \"Result\", "
    'node_count AS "Nodes", edge_count AS "Edges", '
    'ROUND(wall_time_seconds::numeric, 3) AS "Seconds" '
    "FROM gate_metrics ORDER BY created_at DESC LIMIT 20"
)

RUN_VARIABLE_QUERY = (
    "SELECT run_id::text AS value, "
    "COALESCE(label, left(run_id::text, 8)) || ' · ' || COALESCE(origin, 'real') "
    "|| ' · ' || to_char(created_at, 'MM-DD HH24:MI') AS __text "
    "FROM (SELECT DISTINCT ON (run_id) run_id, label, origin, created_at "
    "FROM runs ORDER BY run_id, created_at DESC) recent "
    "ORDER BY created_at DESC LIMIT 100"
)


# ---------------------------------------------------------------- builders


def table_panel(pid, title, x, y, w, h, sql, *, overrides=None, description="", show_header=True):
    """A SQL table panel with optional per-column overrides."""
    return panel(
        pid,
        title,
        "table",
        x,
        y,
        w,
        h,
        description=description,
        targets=[target(sql)],
        options={
            "cellHeight": "sm",
            "showHeader": show_header,
            "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
        },
        fieldConfig={
            "defaults": {
                "custom": {
                    "align": "auto",
                    "cellOptions": {"type": "auto"},
                    "inspect": False,
                    "filterable": False,
                },
                "noValue": "—",
            },
            "overrides": overrides or [],
        },
    )


def stat_panel(
    pid,
    title,
    x,
    y,
    w,
    h,
    sql,
    *,
    unit=None,
    decimals=None,
    thresholds=None,
    color_mode="value",
    graph_mode="none",
    description="",
    mappings=None,
):
    """A SQL stat panel with optional unit, thresholds, and mappings."""
    defaults = {
        "color": {"mode": color_mode if thresholds is None else "thresholds"},
        "mappings": mappings or [],
        "noValue": "—",
        "thresholds": thresholds
        or {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
    }
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    return panel(
        pid,
        title,
        "stat",
        x,
        y,
        w,
        h,
        description=description,
        targets=[target(sql)],
        options={
            "colorMode": color_mode,
            "graphMode": graph_mode,
            "justify": "auto",
            "orientation": "auto",
            "percentChangeColorMode": "standard",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showPercentChange": False,
            "textMode": "auto",
            "wideLayout": True,
        },
        fieldConfig={"defaults": defaults, "overrides": ARM_OVERRIDES_STAT},
    )


ARM_OVERRIDES_STAT = [
    {
        "matcher": {"id": "byName", "options": label},
        "properties": [
            {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
        ],
    }
    for _, label, color in ARMS
]


def timeseries_panel(
    pid, title, x, y, w, h, sql, *, unit=None, decimals=None, description="", min=None, max=None
):
    """A SQL time-series panel with per-arm line overrides."""
    defaults = {
        "color": {"mode": "fixed", "fixedColor": "#8F9BB3"},
        "custom": {
            "axisBorderShow": False,
            "axisCenteredZero": False,
            "axisColorMode": "text",
            "axisLabel": "",
            "axisPlacement": "auto",
            "barAlignment": 0,
            "drawStyle": "line",
            "fillOpacity": 8,
            "gradientMode": "none",
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            "insertNulls": False,
            "lineInterpolation": "linear",
            "lineWidth": 2,
            "pointSize": 5,
            "scaleDistribution": {"type": "linear"},
            "showPoints": "always",
            "spanNulls": False,
            "stacking": {"group": "A", "mode": "none"},
            "thresholdsStyle": {"mode": "off"},
        },
        "mappings": [],
        "noValue": "No runs stored yet",
        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
    }
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    if min is not None:
        defaults["custom"]["axisSoftMin"] = min
    if max is not None:
        defaults["custom"]["axisSoftMax"] = max
    return panel(
        pid,
        title,
        "timeseries",
        x,
        y,
        w,
        h,
        description=description,
        targets=[target(sql, fmt="time_series")],
        options={
            "legend": {
                "calcs": ["lastNotNull"],
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
        },
        fieldConfig={"defaults": defaults, "overrides": ARM_OVERRIDES_LINE},
    )


ARM_OVERRIDES_LINE = [
    {
        "matcher": {"id": "byName", "options": label},
        "properties": [
            {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}},
        ],
    }
    for _, label, color in ARMS
]


def barchart_panel(pid, title, x, y, w, h, sql, description=""):
    """A SQL bar-chart panel with per-arm color overrides."""
    return panel(
        pid,
        title,
        "barchart",
        x,
        y,
        w,
        h,
        description=description,
        targets=[target(sql)],
        options={
            "barRadius": 0,
            "barWidth": 0.7,
            "fullHighlight": False,
            "groupWidth": 0.7,
            "legend": {
                "calcs": [],
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
            },
            "orientation": "auto",
            "showValue": "always",
            "stacking": "none",
            "tooltip": {"hideZeros": False, "mode": "single", "sort": "none"},
            "xTickLabelRotation": 0,
            "xTickLabelSpacing": 0,
        },
        fieldConfig={
            "defaults": {
                "color": {"mode": "fixed", "fixedColor": "#8F9BB3"},
                "custom": {"fillOpacity": 85, "lineWidth": 0},
                "mappings": [],
                "noValue": "—",
                "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
            },
            "overrides": ARM_OVERRIDES_STAT,
        },
    )


PASS_FAIL_OVERRIDES = [
    {
        "matcher": {"id": "byName", "options": label},
        "properties": [
            {
                "id": "mappings",
                "value": [
                    {
                        "options": {
                            "pass": {"color": "#3FB5A5", "index": 0},
                            "fail": {"color": "#E05260", "index": 1},
                        },
                        "type": "value",
                    }
                ],
            },
            {"id": "custom.align", "value": "center"},
            {
                "id": "custom.cellOptions",
                "value": {"type": "color-background", "mode": "basic"},
            },
        ],
    }
    for _, label, _ in ARMS
]

GROUNDING_OVERRIDES = [
    {
        "matcher": {"id": "byName", "options": label},
        "properties": [
            {
                "id": "thresholds",
                "value": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 0.55},
                        {"color": "red", "value": 0.75},
                    ],
                },
            },
            {"id": "custom.align", "value": "center"},
            {
                "id": "custom.cellOptions",
                "value": {"type": "color-background", "mode": "basic"},
            },
            {"id": "unit", "value": "none"},
            {"id": "decimals", "value": 3},
        ],
    }
    for _, label, _ in ARMS
]

SMALL_SAMPLE_NOTE = (
    "Eight tasks is a small sample; read deltas as signal, not proof. "
    "Runs marked seeded in the run context row were produced by the rig seeder, not a model."
)


def build() -> dict:
    """Return the full dashboard document as a dict."""
    panels = [
        header(
            1,
            "Latest run",
            "One invocation, one task list, three contexts: the question alone, the question with "
            "the code map, and the question with the map plus the cited rule text.",
            0,
        ),
        table_panel(
            2,
            "Takeaway",
            0,
            2,
            24,
            4,
            TAKEAWAY_SQL,
            description="One sentence computed from the selected run's tier counts.",
            show_header=False,
            overrides=[
                {
                    "matcher": {"id": "byName", "options": "takeaway"},
                    "properties": [
                        {"id": "custom.align", "value": "center"},
                        {"id": "unit", "value": "string"},
                        {"id": "custom.cellOptions", "value": {"type": "text", "wrapText": True}},
                        {
                            "id": "color",
                            "value": {"fixedColor": "#E8C170", "mode": "fixed"},
                        },
                        {"id": "custom.fillOpacity", "value": 0},
                    ],
                }
            ],
        ),
        table_panel(
            3,
            "Leading arm",
            0,
            6,
            6,
            6,
            LEADING_ARM_SQL,
            description="Best appears only when one arm passed more tasks than the other two.",
            show_header=False,
        ),
        stat_panel(
            4,
            "Pass rate by arm",
            6,
            6,
            6,
            6,
            PASS_RATE_SQL,
            unit="percent",
            decimals=1,
            thresholds={
                "mode": "absolute",
                "steps": [
                    {"color": "red", "value": None},
                    {"color": "yellow", "value": 50},
                    {"color": "green", "value": 80},
                ],
            },
            color_mode="background",
            description=f"Share of the 8 tasks passed. {SMALL_SAMPLE_NOTE}",
        ),
        stat_panel(
            5,
            "Tool calls",
            12,
            6,
            6,
            6,
            TOOL_CALLS_SQL,
            decimals=0,
            description="Total tool calls per arm for the selected run. Fewer is better: "
            "the map should replace repo crawling, not add to it.",
        ),
        stat_panel(
            6,
            "Cost per arm",
            18,
            6,
            6,
            6,
            COST_SQL,
            unit="currencyUSD",
            decimals=3,
            description="Accounted cost per arm: agent tokens, judge tokens, and tool calls "
            "at the configured rates.",
        ),
        table_panel(
            7,
            "Run context",
            0,
            12,
            24,
            4,
            RUN_CONTEXT_SQL,
            description="What produced the selected run. origin=seeded rows come from the rig "
            "seeder with stand-in agents and judges.",
        ),
        header(
            8,
            "Across runs",
            "Every stored run, including the latest. Lines are per arm; the map arms should "
            "hold or raise pass rate while cutting tool calls and cost.",
            16,
        ),
        timeseries_panel(
            9,
            "Pass rate across runs",
            0,
            18,
            12,
            8,
            TREND_SQL["pass"],
            unit="percent",
            decimals=1,
            min=0,
            max=100,
            description="One point per arm per run.",
        ),
        timeseries_panel(
            10,
            "Tool calls across runs",
            12,
            18,
            12,
            8,
            TREND_SQL["tools"],
            decimals=0,
            description="Fewer is better.",
        ),
        timeseries_panel(
            11,
            "Cost across runs",
            0,
            26,
            12,
            8,
            TREND_SQL["cost"],
            unit="currencyUSD",
            decimals=3,
            description="Lower is better.",
        ),
        table_panel(
            12,
            "Change vs previous run",
            12,
            26,
            12,
            8,
            DELTA_SQL,
            description="Selected-arm deltas between the two most recent runs. "
            "Negative tool-call and cost deltas are wins.",
            overrides=[
                {
                    "matcher": {"id": "byName", "options": "Cost Δ (USD)"},
                    "properties": [
                        {"id": "unit", "value": "currencyUSD"},
                        {"id": "decimals", "value": 3},
                        {"id": "custom.align", "value": "right"},
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Tool calls Δ"},
                    "properties": [{"id": "custom.align", "value": "right"}],
                },
                {
                    "matcher": {"id": "byName", "options": "Pass Δ (pp)"},
                    "properties": [{"id": "custom.align", "value": "right"}],
                },
            ],
        ),
        header(
            13,
            "Task detail",
            "The selected run, task by task. Tier 1 asks which symbol a class calls; "
            f"tier 2 asks which corpus rule applies. {SMALL_SAMPLE_NOTE}",
            34,
        ),
        barchart_panel(
            14,
            "Tasks passed by tier",
            0,
            36,
            12,
            9,
            TIER_SQL,
            description="Passes per tier per arm for the selected run.",
        ),
        table_panel(
            15,
            "Per-task matrix",
            12,
            36,
            12,
            9,
            MATRIX_SQL,
            description="Every task against every arm for the selected run.",
            overrides=PASS_FAIL_OVERRIDES,
        ),
        table_panel(
            16,
            "Grounding distance by tier-2 task",
            0,
            45,
            24,
            7,
            GROUNDING_SQL,
            description="Embedding distance between each tier-2 question and its shown chunk. "
            "Lower is better; the value is fixed on the task list and identical across arms.",
            overrides=GROUNDING_OVERRIDES,
        ),
        header(
            17,
            "Repository gate",
            "How the map itself was produced. The sparkline is gate history; "
            "only the gate result is pass or fail.",
            52,
        ),
        stat_panel(
            18,
            "Gate result",
            0,
            54,
            6,
            6,
            GATE_RESULT_SQL,
            thresholds={
                "mode": "absolute",
                "steps": [
                    {"color": "red", "value": None},
                    {"color": "green", "value": 1},
                ],
            },
            mappings=[
                {
                    "options": {
                        "0": {"color": "#E05260", "index": 0, "text": "Not passed"},
                        "1": {"color": "#3FB5A5", "index": 1, "text": "Passed"},
                    },
                    "type": "value",
                }
            ],
            color_mode="background",
            description="Passed means the latest gate produced a graph.",
        ),
        panel(
            19,
            "Mapping time",
            "stat",
            6,
            54,
            6,
            6,
            description="Seconds the call scan took on the latest gate run. "
            "The sparkline is earlier gate runs.",
            targets=[target(GATE_TIME_SQL, fmt="time_series")],
            options={
                "colorMode": "value",
                "graphMode": "area",
                "justify": "auto",
                "orientation": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
                "wideLayout": True,
            },
            fieldConfig={
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "mappings": [],
                    "noValue": "—",
                    "unit": "s",
                    "decimals": 3,
                    "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
                },
                "overrides": [],
            },
        ),
        panel(
            20,
            "Map nodes",
            "stat",
            12,
            54,
            6,
            6,
            description="Nodes in the extracted graph from the latest gate run.",
            targets=[target(GATE_NODES_SQL, fmt="time_series")],
            options={
                "colorMode": "value",
                "graphMode": "area",
                "justify": "auto",
                "orientation": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
                "wideLayout": True,
            },
            fieldConfig={
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "mappings": [],
                    "noValue": "—",
                    "decimals": 0,
                    "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
                },
                "overrides": [],
            },
        ),
        panel(
            21,
            "Map edges",
            "stat",
            18,
            54,
            6,
            6,
            description="Edges in the extracted graph from the latest gate run.",
            targets=[target(GATE_EDGES_SQL, fmt="time_series")],
            options={
                "colorMode": "value",
                "graphMode": "area",
                "justify": "auto",
                "orientation": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
                "wideLayout": True,
            },
            fieldConfig={
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "mappings": [],
                    "noValue": "—",
                    "decimals": 0,
                    "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
                },
                "overrides": [],
            },
        ),
        table_panel(
            22,
            "Gate history",
            0,
            60,
            24,
            7,
            GATE_HISTORY_SQL,
            description="Every stored gate run, newest first.",
        ),
    ]
    return {
        "annotations": {"list": []},
        "description": (
            "Evaluation harness board for the map-writes-the-test rig: one task list answered "
            "bare, with the code map, and with the map plus rules, across every stored run."
        ),
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "panels": panels,
        "refresh": "5s",
        "schemaVersion": 39,
        "tags": ["minirag"],
        "templating": {
            "list": [
                {
                    "current": {
                        "selected": False,
                        "text": "minirag-postgres",
                        "value": "minirag-pg",
                    },
                    "hide": 0,
                    "includeAll": False,
                    "label": "Datasource",
                    "multi": False,
                    "name": "datasource",
                    "options": [],
                    "query": "grafana-postgresql-datasource",
                    "refresh": 1,
                    "regex": "",
                    "skipUrlSync": False,
                    "type": "datasource",
                },
                {
                    "current": {},
                    "datasource": DS,
                    "definition": RUN_VARIABLE_QUERY,
                    "hide": 0,
                    "includeAll": False,
                    "label": "Run",
                    "multi": False,
                    "name": "run",
                    "options": [],
                    "query": RUN_VARIABLE_QUERY,
                    "refresh": 1,
                    "regex": "",
                    "skipUrlSync": False,
                    "sort": 0,
                    "type": "query",
                },
            ]
        },
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {"hidden": True},
        "timezone": "browser",
        "title": "Map Writes the Test",
        "uid": "map-writes-the-test",
        "version": 3,
        "weekStart": "",
    }


def main() -> int:
    """Write the dashboard JSON with a stable layout and a trailing newline."""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
