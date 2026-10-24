"""Read-only preflight and live validation. Never loads application env or credentials."""
import argparse
import json
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from generate_dashboard import APIS, COMPONENTS, targets

HERE = Path(__file__).resolve().parent
NETWORK = "ecommerce-uce_ecommerce-net"
GRAFANA = "http://127.0.0.1:3000"
PROXY = GRAFANA + "/api/datasources/proxy/uid/uce-prometheus"


def command(args):
    result = subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.stdout.strip()


def preflight():
    names = command(["docker", "network", "ls", "--filter", "label=com.docker.compose.project=ecommerce-uce",
                     "--filter", "label=com.docker.compose.network=ecommerce-net", "--format", "{{.Name}}"])
    if names.splitlines() != [NETWORK]:
        raise RuntimeError("The expected application network was not found; do not attach another network")
    with socket.socket() as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 3000))
    return {"network": NETWORK, "port": "127.0.0.1:3000 available", "application_env_loaded": False}


def read_url(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        return response.status, response.headers.get_content_type(), response.read(2_000_000)


def read_json(url):
    return json.loads(read_url(url)[2])


def query(expression):
    data = read_json(PROXY + "/api/v1/query?" + urllib.parse.urlencode({"query": expression}))
    if data.get("status") != "success":
        raise RuntimeError("Prometheus query failed")
    return data["data"]["result"]


def live_validation():
    health = read_json(GRAFANA + "/api/health")
    dashboard = read_json(GRAFANA + "/api/dashboards/uid/uce-stage-a")
    active = read_json(PROXY + "/api/v1/targets")["data"]["activeTargets"]
    probes = [target for target in active if target["labels"].get("job") == "blackbox"]
    states = {row["metric"]["component"]: int(float(row["value"][1])) for row in query("uce:component_state")}
    timeline = next((panel for panel in dashboard.get("dashboard", {}).get("panels", [])
                     if panel.get("type") == "state-timeline"), {})
    fresh = query("uce:probe_success:fresh")
    expected = {(row["labels"]["component"], row["labels"]["probe"]) for row in targets()}
    received = {(row["metric"]["component"], row["metric"]["probe"]) for row in fresh}
    checks = {
        "grafana_database_ok": health.get("database") == "ok",
        "dashboard_provisioned": dashboard.get("dashboard", {}).get("uid") == "uce-stage-a",
        "dashboard_state_mapping_current": timeline.get("fieldConfig", {}).get("defaults", {}).get("color", {}).get("mode") == "fixed",
        "all_targets_scraped": len(probes) == len(expected) and all(t["health"] == "up" for t in probes),
        "all_expected_probes_fresh": received == expected,
        "all_health_probes_successful": len(fresh) == len(expected) and all(float(r["value"][1]) == 1 for r in fresh),
        "six_microservices_visible": set(APIS).issubset(states),
        "all_eight_components_up": set(states) == set(COMPONENTS) and all(s == 3 for s in states.values()),
    }
    # These GETs check the existing public entry point; no login or business write occurs.
    status, content_type, html = read_url("http://127.0.0.1:8090/")
    checks["store_html_ok"] = status == 200 and content_type == "text/html" and b"<!doctype html>" in html.lower()
    status, _, products = read_url("http://127.0.0.1:8090/api/products?limit=1")
    checks["public_catalogue_ok"] = status == 200 and isinstance(json.loads(products).get("items"), list)
    return {"measured_at_utc": datetime.now(timezone.utc).isoformat(), "checks": checks, "states": states,
            "active_blackbox_targets": len(probes), "fresh_probes": len(fresh), "grafana_version": health.get("version"),
            "dashboard": GRAFANA + "/d/uce-stage-a", "all_checks_passed": all(checks.values())}


def main():
    parser = argparse.ArgumentParser(description="Stage A preflight / read-only validation")
    parser.add_argument("action", choices=("preflight", "validate"))
    parser.add_argument("--wait", type=int, default=0, help="seconds to wait for initial startup (maximum 60)")
    args = parser.parse_args()
    if not 0 <= args.wait <= 60:
        parser.error("--wait must be between 0 and 60")
    deadline = time.monotonic() + args.wait
    while True:
        try:
            result = preflight() if args.action == "preflight" else live_validation()
            if args.action == "preflight" or result["all_checks_passed"] or time.monotonic() >= deadline:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                raise SystemExit(0 if result.get("all_checks_passed", True) else 1)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            if time.monotonic() >= deadline:
                # Never print HTTP bodies, container environment or raw subprocess stderr.
                print(f"Validation unavailable ({type(exc).__name__}); check the Stage A containers and URL")
                raise SystemExit(1)
        time.sleep(2)


if __name__ == "__main__":
    main()
