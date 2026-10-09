"""The demo rig ships its Grafana board as committed, parseable files."""

import json
import re
from pathlib import Path

import yaml

from scripts.smoke_rig import FAMILIES

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "grafana" / "dashboards" / "map-writes-the-test.json"
DATASOURCE = ROOT / "grafana" / "provisioning" / "datasources" / "postgres.yaml"
PROVIDERS = ROOT / "grafana" / "provisioning" / "dashboards" / "dashboards.yaml"
COMPOSE = ROOT / "docker-compose.yml"

SINK_TABLES = ("runs", "task_results", "tool_calls", "gate_metrics")

ARM_NAMES = ("Baseline (no map)", "Map only", "Map + rules")
ARM_COLORS = ("#8F9BB3", "#3FB5A5", "#5B8DEF")

PANEL_PLAN = [
    ("Latest run", "text"),
    ("Takeaway", "table"),
    ("Leading arm", "table"),
    ("Pass rate by arm", "stat"),
    ("Tool calls", "stat"),
    ("Cost per arm", "stat"),
    ("Run context", "table"),
    ("Across runs", "text"),
    ("Pass rate across runs", "timeseries"),
    ("Tool calls across runs", "timeseries"),
    ("Cost across runs", "timeseries"),
    ("Change vs previous run", "table"),
    ("Task detail", "text"),
    ("Tasks passed by tier", "barchart"),
    ("Per-task matrix", "table"),
    ("Grounding distance by tier-2 task", "table"),
    ("Repository gate", "text"),
    ("Gate result", "stat"),
    ("Mapping time", "stat"),
    ("Map nodes", "stat"),
    ("Map edges", "stat"),
    ("Gate history", "table"),
]

# Panels that follow the run picker; everything else reads full history.
SELECTED_RUN_PANELS = {
    "Takeaway",
    "Leading arm",
    "Pass rate by arm",
    "Tool calls",
    "Cost per arm",
    "Run context",
    "Tasks passed by tier",
    "Per-task matrix",
    "Grounding distance by tier-2 task",
}


def load_dashboard() -> dict:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def targets(dashboard: dict) -> list[dict]:
    found = []
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            if "rawSql" in target:
                found.append(target)
    return found


def test_dashboard_json_parses_with_the_board_sections():
    dashboard = load_dashboard()
    plan = [(panel["title"], panel["type"]) for panel in dashboard["panels"]]
    assert plan == PANEL_PLAN
    assert dashboard["uid"] == "map-writes-the-test"
    assert dashboard["refresh"] == "5s"
    assert dashboard["timepicker"]["hidden"] is True
    text = DASHBOARD.read_text(encoding="utf-8")
    assert "palette-classic" not in text
    assert "Family " not in text
    for name in ARM_NAMES:
        assert text.count(f'"{name}"') >= 1
    for color in ARM_COLORS:
        assert color in text


def test_the_run_picker_exists_and_reads_the_sink():
    dashboard = load_dashboard()
    variables = {item["name"]: item for item in dashboard["templating"]["list"]}
    assert set(variables) == {"datasource", "run"}
    assert variables["datasource"]["type"] == "datasource"
    assert variables["run"]["type"] == "query"
    assert isinstance(variables["run"]["query"], str)
    assert "FROM runs" in variables["run"]["query"]


def test_selected_run_panels_follow_the_picker_and_history_panels_do_not():
    for panel in load_dashboard()["panels"]:
        sql = " ".join(" ".join(t.get("rawSql", "") for t in panel.get("targets", [])).split())
        if not sql:
            continue
        follows = "${run}" in sql
        assert follows == (panel["title"] in SELECTED_RUN_PANELS), panel["title"]


def test_board_panels_tile_the_grid_without_overlap():
    occupied: dict[tuple[int, int], str] = {}
    for panel in load_dashboard()["panels"]:
        spot = panel["gridPos"]
        assert spot["w"] > 0 and spot["h"] > 0
        assert spot["x"] + spot["w"] <= 24
        for x in range(spot["x"], spot["x"] + spot["w"]):
            for y in range(spot["y"], spot["y"] + spot["h"]):
                assert (x, y) not in occupied, panel["title"]
                occupied[(x, y)] = panel["title"]


def test_every_panel_target_reads_an_existing_sink_table():
    pattern = re.compile(rf"(?:FROM|JOIN)\s+({'|'.join(SINK_TABLES)})\b", re.IGNORECASE)
    for target in targets(load_dashboard()):
        sql = target["rawSql"]
        assert pattern.search(sql), f"target {target['refId']} reads no sink table: {sql}"


def test_dashboard_stays_on_the_local_datasource_without_external_urls():
    text = DASHBOARD.read_text(encoding="utf-8")
    assert "http://" not in text and "https://" not in text
    for target in targets(load_dashboard()):
        assert target["datasource"]["uid"] == "${datasource}"


def test_provisioning_files_exist_and_parse():
    datasource = yaml.safe_load(DATASOURCE.read_text(encoding="utf-8"))
    entry = datasource["datasources"][0]
    assert entry["uid"] == "minirag-pg"
    assert entry["type"] == "postgres"
    assert entry["url"] == "postgres:5432"
    assert entry["jsonData"]["database"] == "minirag"
    assert entry["jsonData"]["sslmode"] == "disable"
    providers = yaml.safe_load(PROVIDERS.read_text(encoding="utf-8"))
    assert providers["providers"][0]["options"]["path"] == "/var/lib/grafana/dashboards"


def test_compose_brings_up_grafana_with_a_pinned_tag():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    grafana = compose["services"]["grafana"]
    assert re.fullmatch(r"grafana/grafana:\d+\.\d+\.\d+", grafana["image"])
    assert "127.0.0.1:3000:3000" in grafana["ports"]
    assert compose["services"]["postgres"] is not None
    env = grafana["environment"]
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "true"


def test_smoke_family_queries_match_dashboard_targets():
    raw_sqls = {" ".join(target["rawSql"].split()) for target in targets(load_dashboard())}
    for key, label, query in FAMILIES:
        assert " ".join(query.split()) in raw_sqls, f"family {key} ({label}) left the board"


def test_the_board_is_reproducible_from_the_builder():
    dashboard = load_dashboard()
    import json as json_module

    from scripts import build_dashboard

    rebuilt = json_module.loads(json_module.dumps(build_dashboard.build()))
    assert dashboard == rebuilt, "edit scripts/build_dashboard.py and rerun it, not the JSON"
