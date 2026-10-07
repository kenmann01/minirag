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


def load_dashboard() -> dict:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def targets(dashboard: dict) -> list[dict]:
    return [target for panel in dashboard["panels"] for target in panel["targets"]]


def test_dashboard_json_parses_with_five_panel_families():
    dashboard = load_dashboard()
    types = [panel["type"] for panel in dashboard["panels"]]
    assert types == ["barchart", "timeseries", "timeseries", "table", "stat", "stat", "stat"]
    assert dashboard["uid"] == "map-writes-the-test"
    assert any(
        item["name"] == "datasource" and item["type"] == "datasource"
        for item in dashboard["templating"]["list"]
    )


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
    for letter, label, query in FAMILIES:
        assert " ".join(query.split()) in raw_sqls, f"family {letter} ({label}) left the board"
