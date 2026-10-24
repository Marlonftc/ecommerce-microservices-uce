"""Read-only JSON/Compose/PromQL validation. Never starts k6 or changes containers."""
import argparse
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from generate_k6_dashboard import HERE, dashboard, expressions

ROOT = HERE.parents[2]
PROXY = "http://127.0.0.1:3000/api/datasources/proxy/uid/uce-prometheus"


def render_query(expression, checked="1"):
    values = {"testid": "validation-no-load", "load_scenario": "scale", "product_replicas": "1|3",
              "measurement_kind": "benchmark", "phase": ".+", "endpoint": ".+", "compare_key": "validation-no-load",
              "run_one": "validation-no-load-one", "run_three": "validation-no-load-three",
              "comparison_checked": checked, "compare_phase": "sustained"}
    expression = expression.replace("$__range", "10m")
    for key, value in values.items():
        expression = re.sub(r"\$\{" + key + r"(?::(?:regex|raw))?\}", lambda _: value, expression)
    if "$" in expression:
        raise ValueError("Unresolved dashboard variable")
    return expression


def compose_config(files, project_directory):
    command = ["docker", "compose", "--env-file", str(HERE / "compose.vars"), "--project-directory", str(project_directory), "--profile", "*"]
    for file in files:
        command += ["-f", str(file)]
    command += ["config", "--no-interpolate", "--no-env-resolution", "--format", "json"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    if result.returncode:
        raise RuntimeError("Compose static validation failed; no config values were printed")
    return json.loads(result.stdout)


def validate_compose():
    obs = compose_config([HERE / "compose.observability.yaml"], HERE)
    prom = obs["services"]["prometheus"]
    if prom.get("ports") or set(prom["networks"]) != {"monitoring"}:
        raise ValueError("Prometheus isolation contract failed")
    if "--web.enable-remote-write-receiver" not in prom["command"]:
        raise ValueError("Remote Write receiver flag missing")
    if prom["mem_limit"] not in ("384m", "402653184", 402653184):
        raise ValueError("Unexpected Prometheus memory limit")
    load = compose_config([ROOT / name for name in ("compose.yaml", "compose.override.example.yaml", "compose.load.yaml", "tests/load/compose.prometheus.yaml")], ROOT)
    k6 = load["services"]["k6"]
    if set(k6["networks"]) != {"ecommerce-net", "monitoring"} or k6.get("ports"):
        raise ValueError("Merged k6 network contract failed")
    if load["networks"]["monitoring"].get("name") != "uce-observability_monitoring":
        raise ValueError("Wrong monitoring network")
    legacy = compose_config([ROOT / name for name in ("compose.yaml", "compose.override.example.yaml", "compose.load.yaml")], ROOT)
    if set(legacy["services"]["k6"]["networks"]) != {"ecommerce-net"}:
        raise ValueError("Legacy k6 must not depend on monitoring")
    smoke = compose_config([HERE / "compose.k6-smoke.yaml"], HERE)
    service = smoke["services"]["k6-smoke"]
    if set(service["networks"]) != {"application", "monitoring"} or service.get("ports"):
        raise ValueError("Smoke network contract failed")
    if any(key.startswith("ADMIN_") for key in service.get("environment", {})):
        raise ValueError("Smoke must not receive credentials")
    for source in (k6, service):
        scripts = next(v for v in source["volumes"] if v["target"] == "/scripts")
        if Path(scripts["source"]).resolve() != ROOT / "tests/load" or not scripts.get("read_only"):
            raise ValueError("Script mounts must resolve to tests/load read-only")
    return {"compose_configs": 4, "prometheus_host_ports": 0, "legacy_networks": ["ecommerce-net"],
            "remote_networks": ["ecommerce-net", "monitoring"], "application_env_loaded": False}


def validate_promql():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    count = 0
    now = int(time.time())
    for panel in dashboard()["panels"]:
        for target in panel.get("targets", []):
            for checked in (("0", "1") if panel["id"] in (40, 41, 42) else ("1",)):
                parameters = {"query": render_query(target["expr"], checked)}
                if target["instant"]:
                    path = "/api/v1/query"
                else:
                    path = "/api/v1/query_range"
                    parameters.update(start=now - 60, end=now, step=15)
                with opener.open(PROXY + path + "?" + urllib.parse.urlencode(parameters), timeout=10) as response:
                    result = json.load(response)
                if result.get("status") != "success":
                    raise ValueError("PromQL validation failed")
                count += 1
    fixtures = validate_promql_fixtures(opener)
    return {"promql_queries_accepted": count, "promql_fixture_checks": fixtures, "validation_testid": "validation-no-load",
            "note": "Syntax/engine validation only. Empty results do not prove k6 delivery."}


def validate_promql_fixtures(opener):
    # These computed vectors exist only during read-only queries, never as stored metrics.
    normal = 'label_replace(vector(9 * time()), "name", "fixture-ok", "", "")'
    error = 'label_replace(vector(time()), "name", "fixture-error", "", "")'
    empty = '(vector(1) < 0)'
    first_error = '(label_replace(vector(time()), "name", "fixture-error", "", "") and on() (vector(time()) >= 600))'
    cases = (("weighted-errors", "errors", normal, error, False, 10),
             ("rps", "rps", normal, error, False, 10),
             ("confirmed-zero-errors", "errors", normal, empty, False, 0),
             ("no-traffic-is-not-zero", "errors", empty, empty, False, None),
             ("stale-activity-is-not-current", "rps", normal, error, True, None),
             ("first-error-is-insufficient-not-zero", "errors", normal, first_error, False, None))
    for name, metric, ok, failed, stale, expected in cases:
        query = render_query(expressions()[metric])
        def counter(match):
            selected = failed if 'expected_response="false"' in match.group(1) else f'({ok} or {failed})'
            return f'({selected})[30s:5s]'
        query = re.sub(r'k6_http_reqs_total\{([^{}]*)\}\[30s\]', counter, query)
        query = re.sub(r'k6_vus\{[^{}]*\}', 'vector(1)', query)
        if stale:
            query = query.replace("<= 15", "<= -1")
        request = urllib.request.Request(PROXY + "/api/v1/query",
                                         data=urllib.parse.urlencode({"query": query, "time": 600}).encode(),
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        with opener.open(request, timeout=10) as response:
            result = json.load(response)
        rows = result.get("data", {}).get("result", [])
        valid = result.get("status") == "success" and (not rows if expected is None else
                len(rows) == 1 and abs(float(rows[0]["value"][1]) - expected) < 0.000001)
        if not valid:
            raise ValueError(f"Isolated PromQL fixture failed: {name}")
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", action="store_true")
    parser.add_argument("--promql", action="store_true")
    args = parser.parse_args()
    actual = json.loads((HERE / "grafana/dashboards/uce-k6.json").read_text(encoding="utf-8"))
    if actual != dashboard():
        parser.exit(1, "The k6 dashboard must be regenerated.\n")
    result = {"dashboard_json_valid": True, "k6_executed": False, "containers_changed": False}
    try:
        if args.compose:
            result.update(validate_compose())
        if args.promql:
            result.update(validate_promql())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Validation unavailable ({type(exc).__name__}); no test was started.\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
