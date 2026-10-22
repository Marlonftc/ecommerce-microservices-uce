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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "tests" / "load" / "results"
BASE_FILES = ["-f", "compose.yaml", "-f", "compose.override.example.yaml"]
LOAD_FILES = [*BASE_FILES, "-f", "compose.load.yaml"]
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


GATEWAY = f"http://127.0.0.1:{env_value('GATEWAY_PORT', '8080')}"


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
        info = json.loads(run(["docker", "inspect", c["Name"]]).stdout)[0]
        for net_name, net in info["NetworkSettings"]["Networks"].items():
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=SCENARIOS)
    parser.add_argument("--replicas", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("-e", "--env", action="append", default=[], help="extra k6 variable, e.g. MAX_VUS=40")
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    status, _, _ = get("/health")
    if status != 200:
        sys.exit(f"the gateway does not answer on {GATEWAY}")
    k6_version = compose("run", "--rm", "k6", "version", files=LOAD_FILES).stdout.strip()
    environment = docker_environment()
    print(f"{k6_version} | {environment}")

    for i in range(1, args.repeat + 1):
        instances = prepare_replicas(args.replicas)
        wait_until_recovered()
        ips = replica_ips()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id = f"{args.scenario}-r{args.replicas}-{stamp}"
        since = (datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"\n=== {run_id} (run {i}/{args.repeat}, {args.replicas} replica(s): {', '.join(instances)})")

        sampler = StatsSampler()
        sampler.start()
        started = time.monotonic()
        k6_args = ["run", "--rm", "-e", f"RUN_ID={run_id}"]
        for extra in args.env:
            k6_args += ["-e", extra]
        result = compose(*k6_args, "k6", "run", "--quiet", f"/scripts/{args.scenario}.js",
                         files=LOAD_FILES, check=False, timeout=900)
        duration = time.monotonic() - started
        sampler.stop_event.set()
        sampler.join(timeout=70)

        # LF endings and no trailing spaces (docker compose pads its progress lines)
        output = "\n".join(line.rstrip() for line in (result.stdout + result.stderr).splitlines()) + "\n"
        (RESULTS / f"{run_id}.log").write_text(output, encoding="utf-8", newline="\n")
        report = {
            "run_id": run_id, "scenario": args.scenario, "date": stamp, "duration_s": round(duration, 1),
            "k6": k6_version, "k6_exit_code": result.returncode,
            "k6_exit_meaning": {0: "ok", 99: "some thresholds were not met"}.get(result.returncode, "error"),
            "extra_env": args.env, "product_service_replicas": args.replicas, "replica_hostnames": instances,
            "environment": environment, "gateway": gateway_demand(run_id, since, ips),
            "docker_stats": sampler.summary(),
        }
        (RESULTS / f"{run_id}-env.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8",
                                                     newline="\n")
        print(result.stdout.strip()[-3000:])
        if result.returncode not in (0, 99):
            print(result.stderr[-2000:])
        g = report["gateway"]
        print(f"gateway: {g['total']} requests, per service {g['per_service_percent']}, "
              f"replicas {g['product_service_replicas']}")
        if i < args.repeat:
            # The login limit (10/min) refills completely in about 30 s
            time.sleep(40 if args.scenario in USES_LOGIN else 10)


if __name__ == "__main__":
    main()
