"""Runs one load scenario with k6 in Docker and records the environment around it.

    python tests/load/run_load.py browse
    python tests/load/run_load.py purchase
    python tests/load/run_load.py mixed
    python tests/load/run_load.py scale --replicas 1 --repeat 3
    python tests/load/run_load.py scale --replicas 3 --repeat 3

For every run it:
- scales product-service to --replicas (default 1) and checks, through the gateway,
  that every replica answers (distinct X-Instance values), without restarting nginx;
- waits until the catalogue answers fast again (recovery from a previous run);
- samples "docker stats" (CPU and memory per container) while k6 runs;
- runs k6 (grafana/k6, version fixed in compose.load.yaml) attached to ecommerce-net;
- counts, from the gateway access log, the public requests of this run per service
  and per product-service replica (filtered by the run id in X-Request-ID).

Results go to tests/load/results/: <run_id>.json (k6 summary, written by the script
itself), <run_id>.log (k6 output) and <run_id>-env.json (this file's measurements).
Only the Python standard library is used. Nothing secret is read or written:
the admin credentials reach k6 through compose.load.yaml from the root .env.
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from k6_observability import (APPLICATION_NETWORK, MONITORING_NETWORK, PROXY, RW_ENV,
                              comparison_context, comparison_key, export_arguments, validate_extra_env)

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "tests" / "load" / "results"
BASE_FILES = ["-f", "compose.yaml", "-f", "compose.override.example.yaml"]
LOAD_FILES = [*BASE_FILES, "-f", "compose.load.yaml"]
PROMETHEUS_FILES = [*LOAD_FILES, "-f", "tests/load/compose.prometheus.yaml"]
SCENARIOS = ("browse", "purchase", "mixed", "scale")
USES_LOGIN = ("browse", "purchase", "mixed")
SERVICE_BY_PREFIX = [
    ("/api/auth", "user-service"), ("/api/users", "user-service"),
    ("/api/products", "product-service"), ("/api/inventory", "inventory-service"),
    ("/api/cart", "order-service"), ("/api/orders", "order-service"), ("/api/admin", "order-service"),
    ("/api/payments", "payment-service"), ("/api/notifications", "notification-service"),
]


def env_value(name: str, default: str) -> str:
    """Reads one non-secret value (GATEWAY_PORT) from the root .env without printing anything."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip() or default
    return default


# Resolve the legacy port only in main(); importing tests must never read .env.
GATEWAY = "http://127.0.0.1:8080"


def read_prometheus(path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(PROXY + path, timeout=8) as response:
        return json.loads(response.read(2_000_000))


def remote_preflight():
    for name, project, network in ((APPLICATION_NETWORK, "ecommerce-uce", "ecommerce-net"),
                                   (MONITORING_NETWORK, "uce-observability", "monitoring")):
        actual = run(["docker", "network", "ls", "--filter", f"label=com.docker.compose.project={project}",
                      "--filter", f"label=com.docker.compose.network={network}", "--format", "{{.Name}}"]).stdout.strip()
        if actual != name:
            raise RuntimeError("Expected Docker network unavailable; no load was started")
    flags = read_prometheus("/api/v1/status/flags")
    if flags.get("status") != "success" or flags.get("data", {}).get("web.enable-remote-write-receiver") != "true":
        raise RuntimeError("Prometheus receiver is disabled or unavailable; deploy it before running k6")


def application_images():
    allowed = {"gateway", "frontend", "user-service", "product-service", "inventory-service", "order-service",
               "payment-service", "notification-service", "order-worker"}
    rows = run(["docker", "ps", "--filter", "label=com.docker.compose.project=ecommerce-uce", "--format",
                '{{.ID}} {{.Label "com.docker.compose.service"}}']).stdout.splitlines()
    images = {}
    for row in rows:
        identity, service = row.split(maxsplit=1)
        if service in allowed:
            image = run(["docker", "inspect", "--format", "{{.Image}}", identity]).stdout.strip()
            images.setdefault(service, set()).add(image)
    if set(images) != allowed:
        raise RuntimeError("Application image metadata is incomplete")
    return {service: sorted(values) for service, values in sorted(images.items())}


def reserve_run_id(scenario, replicas, stamp):
    base = f"{scenario}-r{replicas}-{stamp}"
    for attempt in range(20):
        run_id = base if not attempt else f"{base}-{uuid.uuid4().hex[:8]}"
        paths = [RESULTS / f"{run_id}{suffix}" for suffix in (".json", ".log", "-env.json")]
        reservation = RESULTS / f".{run_id}.lock"
        try:
            with reservation.open("x", encoding="utf-8"):
                pass
        except FileExistsError:
            continue
        if any(p.exists() for p in paths) or (RESULTS / ".pending" / run_id).exists():
            reservation.unlink()
            continue
        return run_id, reservation
    raise RuntimeError("Could not reserve a unique run id; no reports were overwritten")


def k6_command(scenario, run_id, replicas, extra, prometheus=False, key=None, staging=False):
    validate_extra_env(extra, prometheus)
    args = ["run", "--rm", "-e", f"RUN_ID={run_id}"]
    for entry in extra:
        args += ["-e", entry]
    if staging:
        args += ["-e", f"RESULTS_DIR=/results/.pending/{run_id}"]
    if prometheus:
        for name, value in RW_ENV.items():
            args += ["-e", f"{name}={value}"]
    args += ["k6", "run", "--quiet"]
    if prometheus:
        args += export_arguments(run_id, scenario, replicas, key=key)[0]
    return [*args, f"/scripts/{scenario}.js"]


def export_observation(run_id, started_ms, finished_ms):
    # A successful k6 exit is not proof of delivery. Check recorded samples separately.
    from urllib.parse import urlencode
    expressions = {
        "vus": f'k6_vus{{testid="{run_id}"}}',
        "requests": f'k6_http_reqs_total{{testid="{run_id}"}}',
        "histograms": f'k6_http_req_duration_seconds{{testid="{run_id}"}}',
    }
    observed = {}
    try:
        for name, expression in expressions.items():
            query = urlencode({"query": expression, "start": started_ms / 1000,
                               "end": finished_ms / 1000 + 5, "step": 5})
            reply = read_prometheus("/api/v1/query_range?" + query)
            if reply.get("status") != "success":
                return "unverified", {}
            rows = reply.get("data", {}).get("result", [])
            observed[name] = any(row.get("histograms") if name == "histograms" else row.get("values") for row in rows)
        return ("samples_observed" if all(observed.values()) else "incomplete"), observed
    except (OSError, ValueError, KeyError, TypeError):
        return "unverified", {}


def write_new(path, text):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def promote_summary(run_id, scenario):
    directory = RESULTS / ".pending" / run_id
    source = directory / f"{run_id}.json"
    if not source.is_file():
        return "missing; inspect the isolated .pending directory"
    raw = source.read_bytes()
    try:
        value = json.loads(raw)
    except ValueError:
        return "invalid; retained in .pending"
    if not isinstance(value, dict) or value.get("run_id") != run_id or value.get("scenario") != scenario:
        return "identity mismatch; retained in .pending"
    with (RESULTS / source.name).open("xb") as destination:
        destination.write(raw)
    source.unlink()
    if not any(directory.iterdir()):
        directory.rmdir()
    return "verified"


def run(cmd: list[str], check: bool = True, timeout: float = 600) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                            encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd[:6])}...\n{result.stderr[-1500:]}")
    return result


def compose(*args: str, files=BASE_FILES, **kw) -> subprocess.CompletedProcess:
    return run(["docker", "compose", *files, *args], **kw)


def get(path: str, timeout: float = 10) -> tuple[int, dict, float]:
    start = time.monotonic()
    try:
        with urllib.request.urlopen(GATEWAY + path, timeout=timeout) as r:
            r.read()
            return r.status, {k.lower(): v for k, v in r.headers.items()}, time.monotonic() - start
    except urllib.error.HTTPError as e:
        return e.code, {}, time.monotonic() - start
    except Exception:
        return 0, {}, time.monotonic() - start


def wait(check, timeout: float, what: str, interval: float = 2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(interval)
    sys.exit(f"timeout after {timeout:.0f} s waiting for {what}")


def product_containers() -> list[dict]:
    out = compose("ps", "--format", "json", "product-service").stdout.strip()
    rows = json.loads(out) if out.startswith("[") else [json.loads(l) for l in out.splitlines() if l.strip()]
    return [r for r in rows if r.get("State") == "running"]


def replica_ips() -> dict[str, str]:
    result = {}
    for c in product_containers():
        networks = json.loads(run(["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", c["Name"]]).stdout)
        for net_name, net in networks.items():
            if net_name.endswith("_ecommerce-net"):
                result[net["IPAddress"]] = c["Name"]
    return result


def prepare_replicas(n: int) -> list[str]:
    compose("up", "-d", "--no-deps", "--no-recreate", "--scale", f"product-service={n}", "product-service")
    wait(lambda: len([c for c in product_containers() if c.get("Health") == "healthy"]) == n, 120,
         f"{n} healthy product-service replicas")

    def all_answer():
        seen = {get("/api/products?limit=1")[1].get("x-instance") for _ in range(6 * n)}
        seen.discard(None)
        return len(seen) == n

    wait(all_answer, 60, f"{n} distinct X-Instance values through the gateway")
    return sorted({get("/api/products?limit=1")[1].get("x-instance") for _ in range(6 * n)} - {None})


def wait_until_recovered():
    """5 fast answers in a row: a previous saturation has fully drained."""
    def fast():
        return all((lambda s: s[0] == 200 and s[2] < 1.0)(get("/api/products?limit=20", 40)) for _ in range(5))
    wait(fast, 600, "the catalogue to answer quickly again", interval=5)


class StatsSampler(threading.Thread):
    """docker stats every few seconds for the project containers."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples: dict[str, list[tuple[float, float]]] = {}
        self.stop_event = threading.Event()

    @staticmethod
    def mib(text: str) -> float:
        m = re.match(r"([\d.]+)\s*([KMG]i?B)", text.strip())
        if not m:
            return 0.0
        value, unit = float(m.group(1)), m.group(2)
        return value / 1024 if unit.startswith("K") else value * 1024 if unit.startswith("G") else value

    def run(self):
        while not self.stop_event.is_set():
            out = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], check=False, timeout=60).stdout
            for line in out.splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = row.get("Name", "")
                if name.startswith("ecommerce-uce-"):
                    cpu = float(row.get("CPUPerc", "0%").rstrip("%") or 0)
                    mem = self.mib(row.get("MemUsage", "0MiB / 0MiB").split("/")[0])
                    self.samples.setdefault(name, []).append((cpu, mem))
            self.stop_event.wait(3)

    def summary(self) -> dict:
        return {name: {"cpu_avg_percent": round(statistics.mean(c for c, _ in s), 1),
                       "cpu_max_percent": round(max(c for c, _ in s), 1),
                       "mem_max_mib": round(max(m for _, m in s), 1), "samples": len(s)}
                for name, s in sorted(self.samples.items()) if s}


def gateway_demand(run_id: str, since: str, ips: dict[str, str]) -> dict:
    out = compose("logs", "--no-log-prefix", "--since", since, "gateway", timeout=300).stdout
    per_service, statuses, replicas, total = Counter(), Counter(), Counter(), 0
    for line in out.splitlines():
        if not line.startswith("{") or f'"request_id":"{run_id}-' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1
        path = entry.get("path", "")
        service = next((s for p, s in SERVICE_BY_PREFIX if path == p or path.startswith(p + "/")), "other")
        per_service[service] += 1
        statuses[str(entry.get("status"))] += 1
        if service == "product-service":
            addr = entry.get("upstream_addr", "").split(":")[0]
            replicas[ips.get(addr, addr or "none")] += 1
    return {
        "note": "Public requests that reached the gateway in this run. Calls between "
                "microservices do not pass through the gateway and are not included.",
        "total": total,
        "per_service": dict(per_service.most_common()),
        "per_service_percent": {k: round(100 * v / total, 1) for k, v in per_service.most_common()} if total else {},
        "status": dict(sorted(statuses.items())),
        "product_service_replicas": dict(replicas.most_common()),
    }


def docker_environment() -> dict:
    info = json.loads(run(["docker", "info", "--format", "{{json .}}"]).stdout)
    return {"docker_server": info.get("ServerVersion"), "os": info.get("OperatingSystem"),
            "cpus": info.get("NCPU"), "memory_gib": round(info.get("MemTotal", 0) / 2 ** 30, 1)}


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=SCENARIOS)
    parser.add_argument("--replicas", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("-e", "--env", action="append", default=[], help="extra k6 variable, e.g. MAX_VUS=40")
    parser.add_argument("--prometheus", action="store_true", help="opt-in internal Prometheus Remote Write")
    args = parser.parse_args(argv)
    if args.replicas < 1 or args.repeat < 1:
        parser.error("--replicas and --repeat must be positive")
    try:
        validate_extra_env(args.env, args.prometheus)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv=None):
    global GATEWAY
    args = parse_args(argv)
    GATEWAY = f"http://127.0.0.1:{env_value('GATEWAY_PORT', '8080')}"
    files = PROMETHEUS_FILES if args.prometheus else LOAD_FILES
    if args.prometheus:
        try:
            remote_preflight()
        except (OSError, ValueError, RuntimeError) as exc:
            sys.exit(f"Remote Write preflight failed ({type(exc).__name__}); no load was started")

    RESULTS.mkdir(parents=True, exist_ok=True)
    status, _, _ = get("/health")
    if status != 200:
        sys.exit(f"the gateway does not answer on {GATEWAY}")
    k6_version = compose("run", "--rm", "k6", "version", files=files).stdout.strip()
    environment = docker_environment()
    print(f"{k6_version} | {environment}")

    for i in range(1, args.repeat + 1):
        instances = prepare_replicas(args.replicas)
        wait_until_recovered()
        ips = replica_ips()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id, reservation = reserve_run_id(args.scenario, args.replicas, stamp)
        since = (datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"\n=== {run_id} (run {i}/{args.repeat}, {args.replicas} replica(s): {', '.join(instances)})")

        sampler = StatsSampler()
        try:
            metadata = comparison_context(args.scenario, args.env, environment, k6_version, application_images()) if args.prometheus else None
            key = comparison_key(metadata) if metadata else None
            (RESULTS / ".pending" / run_id).mkdir(parents=True)
            sampler.start()
            started_at = datetime.now(timezone.utc)
            started = time.monotonic()
            try:
                result = compose(*k6_command(args.scenario, run_id, args.replicas, args.env, args.prometheus, key, staging=True),
                                 files=files, check=False, timeout=900)
                duration = time.monotonic() - started
                finished_at = datetime.now(timezone.utc)
            finally:
                sampler.stop_event.set()
                sampler.join(timeout=70)

            save_report(args, run_id, stamp, since, ips, instances, k6_version, environment, sampler,
                        result, duration, started_at, finished_at, metadata, key, promote_summary(run_id, args.scenario))
        finally:
            reservation.unlink()

        if i < args.repeat:
            # The login limit (10/min) refills completely in about 30 s.
            time.sleep(40 if args.scenario in USES_LOGIN else 10)


def save_report(args, run_id, stamp, since, ips, instances, k6_version, environment, sampler,
            result, duration, started_at, finished_at, metadata, key, summary_status):

    # LF endings and no trailing spaces (docker compose pads its progress lines)
    output = "\n".join(line.rstrip() for line in (result.stdout + result.stderr).splitlines()) + "\n"
    write_new(RESULTS / f"{run_id}.log", output)
    report = {
        "run_id": run_id, "scenario": args.scenario, "date": stamp, "duration_s": round(duration, 1),
        "summary_status": summary_status,
        "k6": k6_version, "k6_exit_code": result.returncode,
        "k6_exit_meaning": {0: "ok", 99: "some thresholds were not met"}.get(result.returncode, "error"),
        "extra_env": args.env, "product_service_replicas": args.replicas, "replica_hostnames": instances,
        "environment": environment, "gateway": gateway_demand(run_id, since, ips),
        "docker_stats": sampler.summary(),
    }
    if args.prometheus:
        start_ms = int(started_at.timestamp() * 1000)
        finish_ms = int(finished_at.timestamp() * 1000)
        export_status, observed = export_observation(run_id, start_ms, finish_ms)
        report.update(started_at_utc=started_at.isoformat(), finished_at_utc=finished_at.isoformat(),
                      started_at_epoch_ms=start_ms, finished_at_epoch_ms=finish_ms)
        report["observability"] = {
            "output": "experimental-prometheus-rw", "endpoint": RW_ENV["K6_PROMETHEUS_RW_SERVER_URL"],
            "format": "native-histogram-v1", "push_interval_s": 5,
            "labels": export_arguments(run_id, args.scenario, args.replicas, key=key)[1],
            "comparison_key": key, "comparison_context": metadata,
            "export_status": export_status, "observed_metrics": observed,
            "note": "Samples observed does not prove lossless delivery or final threshold success.",
        }
        print(f"Remote Write: {export_status}; k6 exit: {result.returncode} (independent evidence)")
    write_new(RESULTS / f"{run_id}-env.json", json.dumps(report, indent=2) + "\n")
    print(result.stdout.strip()[-3000:])
    if result.returncode not in (0, 99):
        print(result.stderr[-2000:])
    g = report["gateway"]
    print(f"gateway: {g['total']} requests, per service {g['per_service_percent']}, "
          f"replicas {g['product_service_replicas']}")

if __name__ == "__main__":
    main()
