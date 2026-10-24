"""Non-secret k6 export metadata and read-only comparison validation (stdlib only)."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
APPLICATION_NETWORK = "ecommerce-uce_ecommerce-net"
MONITORING_NETWORK = "uce-observability_monitoring"
PROXY = "http://127.0.0.1:3000/api/datasources/proxy/uid/uce-prometheus"
REMOTE_URL = "http://prometheus:9090/api/v1/write"
SYSTEM_TAGS = "status,method,name,group,check,error_code,scenario,expected_response"
RW_ENV = {
    "K6_PROMETHEUS_RW_SERVER_URL": REMOTE_URL,
    "K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM": "true",
    "K6_PROMETHEUS_RW_PUSH_INTERVAL": "5s",
    "K6_PROMETHEUS_RW_STALE_MARKERS": "true",
    "K6_NO_USAGE_REPORT": "true",
}


def validate_extra_env(entries, prometheus=False):
    seen = set()
    for entry in entries:
        key, separator, _ = entry.partition("=")
        if not separator or not key or not key.replace("_", "a").isalnum():
            raise ValueError("extra variables must use NAME=value")
        if key in seen:
            raise ValueError(f"duplicate extra variable: {key}")
        seen.add(key)
        if key in {"RUN_ID", "RESULTS_DIR"} or (prometheus and (key in {"K6_OUT", "K6_TAGS", "K6_SYSTEM_TAGS"}
                                               or key.startswith("K6_PROMETHEUS_RW_"))):
            raise ValueError(f"{key} is controlled by the runner")


def comparison_context(scenario, extra_env, environment, k6_version, images, directory=HERE):
    # Replicas are deliberately excluded; everything else must match conservatively.
    sources = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
               for name in (f"{scenario}.js", "lib.js", "purchase_flow.js")}
    return {"load_scenario": scenario, "extra_env": sorted(extra_env), "environment": environment,
            "k6": k6_version, "application_images": images, "workload_sha256": sources,
            "export_format": "native-histogram-v1", "system_tags": SYSTEM_TAGS, "push_interval_s": 5}


def comparison_key(context):
    return hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def export_arguments(run_id, scenario, replicas, kind="benchmark", key=None):
    labels = {"testid": run_id, "load_scenario": scenario, "product_replicas": str(replicas),
              "measurement_kind": kind}
    if key:
        labels["comparison_key"] = key
    args = ["--out", "experimental-prometheus-rw", "--system-tags", SYSTEM_TAGS]
    for name, value in labels.items():
        args += ["--tag", f"{name}={value}"]
    return args, labels


def verify_comparison(one_id, three_id, directory=HERE / "results"):
    contexts = []
    for run_id, replicas in ((one_id, 1), (three_id, 3)):
        if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in run_id):
            raise ValueError("invalid run id")
        summary = json.loads((directory / f"{run_id}.json").read_text(encoding="utf-8-sig"))
        context = json.loads((directory / f"{run_id}-env.json").read_text(encoding="utf-8-sig"))
        if not isinstance(summary, dict) or not isinstance(context, dict):
            raise ValueError("reports must be JSON objects")
        if any(obj.get("run_id") != run_id or obj.get("scenario") != "scale" for obj in (summary, context)):
            raise ValueError("run id or scale scenario mismatch")
        if context.get("product_service_replicas") != replicas or context.get("k6_exit_code") != 0:
            raise ValueError("expected 1/3 replicas and successful k6 exits")
        thresholds = [threshold for metric in summary.get("metrics", {}).values()
                      for threshold in metric.get("thresholds", {}).values()]
        if not thresholds or any(t.get("ok") is not True for t in thresholds):
            raise ValueError("missing or failed final thresholds")
        obs = context.get("observability", {})
        metadata = obs.get("comparison_context")
        if not isinstance(metadata, dict) or obs.get("comparison_key") != comparison_key(metadata):
            raise ValueError("missing or inconsistent Remote Write comparison metadata")
        labels = obs.get("labels", {})
        expected = {"testid": run_id, "load_scenario": "scale", "product_replicas": str(replicas),
                    "measurement_kind": "benchmark", "comparison_key": obs["comparison_key"]}
        if labels != expected or obs.get("output") != "experimental-prometheus-rw":
            raise ValueError("run was not exported with matching labels")
        if obs.get("export_status") != "samples_observed":
            raise ValueError("Remote Write samples have not been confirmed")
        if not summary.get("scale_config"):
            raise ValueError("missing recorded scale phases")
        contexts.append((context, summary, metadata))
    if contexts[0][2] != contexts[1][2] or contexts[0][1]["scale_config"] != contexts[1][1]["scale_config"]:
        raise ValueError("workload, environment, images or phase configuration differ")
    return "http://127.0.0.1:3000/d/uce-k6?" + urlencode({
        "var-measurement_kind": "benchmark", "var-load_scenario": "scale", "var-testid": one_id,
        "var-product_replicas": "$__all", "var-endpoint": "$__all", "var-phase": "$__all",
        "var-compare_key": comparison_key(contexts[0][2]), "var-run_one": one_id, "var-run_three": three_id,
        "var-comparison_checked": "1", "from": min(c[0]["started_at_epoch_ms"] for c in contexts),
        "to": max(c[0]["finished_at_epoch_ms"] for c in contexts)})


def main():
    parser = argparse.ArgumentParser(description="Read-only validation of completed 1 vs 3 k6 runs")
    parser.add_argument("one")
    parser.add_argument("three")
    args = parser.parse_args()
    try:
        print(verify_comparison(args.one, args.three))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Comparison unavailable ({type(exc).__name__}); inspect the non-secret run metadata.\n")


if __name__ == "__main__":
    main()
