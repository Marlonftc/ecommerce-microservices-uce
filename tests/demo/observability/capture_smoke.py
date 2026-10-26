"""Read-only smoke telemetry capture. Never starts k6 or sends business requests."""
import argparse
import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from generate_k6_dashboard import dashboard

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROXY = "http://127.0.0.1:3000/api/datasources/proxy/uid/uce-prometheus"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
METRICS = ("k6_http_reqs_total", "k6_vus", "k6_http_req_duration_seconds")


def query(expression, **parameters):
    parameters = {"query": expression, **parameters}
    with OPENER.open(PROXY + "/api/v1/query?" + urllib.parse.urlencode(parameters), timeout=8) as response:
        reply = json.load(response)
    if reply.get("status") != "success":
        raise ValueError("Prometheus query failed")
    return reply["data"]


def save(directory, name, value):
    (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def rendered(expression, run_id, replicas):
    substitutions = {"testid": run_id, "load_scenario": "integration_smoke", "product_replicas": str(replicas),
                     "measurement_kind": "integration_smoke", "phase": "smoke", "endpoint": "catalogue_smoke"}
    for name, value in substitutions.items():
        expression = re.sub(r"\$\{" + name + r"(?::regex)?\}", lambda _: value, expression)
    return expression.replace("$__range", "2m")


def watch(run_id, replicas, directory, seconds):
    cards = {p["id"]: p["targets"][0]["expr"] for p in dashboard()["panels"] if p["id"] in (10, 11, 12, 13)}
    observations = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        observation = {"observed_at_utc": datetime.now(timezone.utc).isoformat(), "cards": {}}
        for identity, expression in cards.items():
            try:
                observation["cards"][str(identity)] = query(rendered(expression, run_id, replicas))
            except (OSError, ValueError) as exc:
                observation["cards"][str(identity)] = {"unavailable": type(exc).__name__}
        observations.append(observation)
        save(directory, "live-observations.json", {"testid": run_id, "source": "real PromQL instant evaluations",
                                                  "observations": observations})
        values = {name: [row.get("value") for row in data.get("result", [])]
                  for name, data in observation["cards"].items()}
        print(json.dumps({"at": observation["observed_at_utc"], "cards": values}), flush=True)
        time.sleep(min(5, max(0, deadline - time.monotonic())))


def scalar_values(rows):
    return [float(value) for row in rows for _, value in row.get("values", [])]


def verify(run_id, replicas, directory):
    results = ROOT / "tests/load/results/smoke"
    context = json.loads((results / f"{run_id}-env.json").read_text(encoding="utf-8-sig"))
    summary = json.loads((results / f"{run_id}.json").read_text(encoding="utf-8-sig"))
    start = datetime.fromisoformat(context["started_at_utc"]).timestamp()
    finish = datetime.fromisoformat(context["finished_at_utc"]).timestamp()
    # Range selectors return actual stored timestamps, not a reconstructed sampling grid.
    lookback = math.ceil(finish - start + 20)
    end = finish + 10
    raw = {metric: query(f'{metric}{{testid="{run_id}"}}[{lookback}s]', time=end) for metric in METRICS}
    save(directory, "prometheus-raw-samples.json", {"testid": run_id, "range_end_epoch": end,
                                                 "source": "raw stored range-vector samples", "metrics": raw})
    rows = {name: raw[name]["result"] for name in METRICS}
    request_rows, vu_rows, duration_rows = (rows[name] for name in METRICS)
    counts = [max(float(v) for _, v in row.get("values", [])) for row in request_rows if row.get("values")]
    request_count = sum(counts) if counts else None
    vu_values = scalar_values(vu_rows)
    peak_vus = max(vu_values) if vu_values else None
    histograms = []
    for row in duration_rows:
        samples = row.get("histograms", [])
        if samples:
            timestamp, histogram = max(samples, key=lambda pair: (float(pair[1]["count"]), pair[0]))
            histograms.append({"timestamp": timestamp, "labels": row["metric"], **histogram})
    histogram_count = sum(float(h["count"]) for h in histograms) if histograms else None
    histogram_sum_seconds = sum(float(h["sum"]) for h in histograms) if histograms else None
    mean_ms = histogram_sum_seconds / histogram_count * 1000 if histogram_count else None
    metric = summary.get("metrics", {})
    summary_count = metric.get("http_reqs", {}).get("values", {}).get("count")
    summary_avg = metric.get("http_req_duration", {}).get("values", {}).get("avg")
    thresholds = [v for m in metric.values() for v in m.get("thresholds", {}).values()]
    expected = {"testid": run_id, "load_scenario": "integration_smoke", "product_replicas": str(replicas),
                "measurement_kind": "integration_smoke"}
    all_rows = request_rows + vu_rows + duration_rows
    forbidden = {"url", "user_id", "order_id", "request_id", "vu", "iter", "token", "password"}
    current = {name: query(f'{name}{{testid="{run_id}"}}') for name in METRICS}
    derived = {}
    for panel in dashboard()["panels"]:
        if panel["id"] in (10, 11, 12, 13, 20, 21, 22, 23, 24, 25):
            expression = rendered(panel["targets"][0]["expr"], run_id, replicas)
            # Keep evaluations distinct from raw timestamps captured above.
            with OPENER.open(PROXY + "/api/v1/query_range?" + urllib.parse.urlencode({
                    "query": expression, "start": start, "end": end, "step": 5}), timeout=8) as response:
                derived[str(panel["id"])] = json.load(response)
    save(directory, "dashboard-evaluations.json", {"testid": run_id, "source": "derived PromQL on real samples",
                                               "evaluation_step_s": 5, "panels": derived})
    checks = {
        "k6_exit_zero": context.get("k6_exit_code") == 0,
        "summary_identity_matches": summary.get("run_id") == run_id and summary.get("scenario") == "integration_smoke",
        "summary_eight_requests": summary_count == 8,
        "summary_no_http_errors": metric.get("http_req_failed", {}).get("values", {}).get("rate") == 0,
        "all_recorded_thresholds_passed": bool(thresholds) and all(t.get("ok") is True for t in thresholds),
        "prometheus_eight_requests": request_count == 8,
        "prometheus_peak_one_vu": peak_vus == 1,
        "native_histograms_received": bool(histograms) and histogram_count == 8,
        "seconds_to_ms_matches_summary_mean": mean_ms is not None and summary_avg is not None and
            math.isclose(mean_ms, summary_avg, rel_tol=1e-6, abs_tol=1e-5),
        "global_labels_match": bool(all_rows) and all(all(r["metric"].get(k) == v for k, v in expected.items()) for r in all_rows),
        "only_catalogue_smoke_phase": bool(request_rows) and all(r["metric"].get("phase") == "smoke" and
            r["metric"].get("name") == "catalogue_smoke" and r["metric"].get("method") == "GET" for r in request_rows),
        "no_forbidden_labels": bool(all_rows) and all(not (forbidden & set(r["metric"])) for r in all_rows),
        "stale_markers_hide_finished_activity": all(not r["result"] for r in current.values()),
        "historical_dashboard_has_real_curves": all(derived[str(p)].get("status") == "success" and
            any(row.get("values") for row in derived[str(p)].get("data", {}).get("result", [])) for p in (20, 21, 22, 23)),
    }
    report = {"testid": run_id, "measurement_kind": "integration_smoke", "verified_at_utc": datetime.now(timezone.utc).isoformat(),
              "checks": checks, "all_checks_passed": all(checks.values()), "expected_labels": expected,
              "prometheus_request_count": request_count, "peak_vus": peak_vus, "native_histogram_count": histogram_count,
              "native_histogram_sum_seconds": histogram_sum_seconds, "native_mean_ms": mean_ms,
              "summary_mean_ms": summary_avg, "summary_p95_ms": metric.get("http_req_duration", {}).get("values", {}).get("p(95)"),
              "raw_series": {name: len(values) for name, values in rows.items()},
              "stored_samples": {name: sum(len(row.get("values", [])) + len(row.get("histograms", [])) for row in values)
                                 for name, values in rows.items()},
              "note": "Integration evidence only; histogram window p95 is not the exact final JSON p95."}
    save(directory, "verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_checks_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--replicas", type=int, required=True)
    parser.add_argument("--watch-seconds", type=int, default=0)
    args = parser.parse_args()
    if not re.fullmatch(r"smoke-[A-Za-z0-9_-]+", args.run_id) or args.replicas < 1 or not 0 <= args.watch_seconds <= 90:
        parser.error("Invalid smoke identity, replicas or observation duration")
    directory = HERE / "evidence/smoke" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    if args.watch_seconds:
        watch(args.run_id, args.replicas, directory, args.watch_seconds)
        return 0
    return verify(args.run_id, args.replicas, directory)


if __name__ == "__main__":
    raise SystemExit(main())
