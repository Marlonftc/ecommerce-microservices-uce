"""Live monitor for demonstrations: what the system is doing, refreshed every second.

Shows, through the gateway (the only published port), like any client:
- the state of each microservice and of the frontend (UP / DOWN, HTTP code, time),
- how the gateway distributes the catalogue requests among the product-service
  replicas (header X-Instance, last 60 requests), as a bar chart,
- requests per second, errors and response times (p50 / p95) of the catalogue,
- the containers of each service according to Docker Compose (running / healthy).

It only reads: it creates no data, uses no credentials and changes nothing.
Run it in its own window during a load test, a scale-out or a stopped service:

    backend\\order-service\\.venv\\Scripts\\python.exe tests\\demo\\monitor.py
    (Ctrl+C to stop)
"""
import json
import os
import subprocess
import threading
import time
from collections import Counter, deque
from pathlib import Path

import httpx2
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
PORT = os.environ.get("GATEWAY_PORT") or dotenv_values(ROOT / ".env").get("GATEWAY_PORT") or "8080"
GATEWAY = f"http://127.0.0.1:{PORT}"

# A route of each service that answers without a token: any answer except the gateway's own
# 502/504 means the gateway reaches the service (401 is the service itself asking for a token)
PROBES = {
    "frontend": "/",
    "user-service": "/api/users/me",
    "product-service": "/api/products?limit=1",
    "inventory-service": "/api/inventory/1",
    "order-service": "/api/cart",
    "payment-service": "/api/payments",
    "notification-service": "/api/notifications",
}
COMPOSE_SERVICES = ["gateway", "frontend", "user-service", "product-service", "inventory-service", "order-service",
                    "order-worker", "payment-service", "notification-service", "postgres"]

GREEN, RED, YELLOW, DIM, BOLD, RESET = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[2m", "\x1b[1m", "\x1b[0m"

catalogue = deque(maxlen=60)          # (time, instance or None, status, seconds)
lock = threading.Lock()
containers: dict[str, list[tuple[str, str]]] = {}


def catalogue_traffic(stop: threading.Event) -> None:
    """About 5 catalogue requests per second, like a light visitor; the load comes from k6."""
    with httpx2.Client(base_url=GATEWAY, timeout=10) as client:
        while not stop.is_set():
            start = time.monotonic()
            try:
                r = client.get("/api/products", params={"limit": 1})
                entry = (time.time(), r.headers.get("x-instance"), r.status_code, time.monotonic() - start)
            except Exception:
                entry = (time.time(), None, 0, time.monotonic() - start)
            with lock:
                catalogue.append(entry)
            stop.wait(0.2)


def docker_state(stop: threading.Event) -> None:
    """Containers per service, every 3 s (optional: skipped if docker is not available)."""
    while not stop.is_set():
        try:
            out = subprocess.run(["docker", "compose", "ps", "-a", "--format", "json"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            rows = json.loads(out) if out.startswith("[") else [json.loads(x) for x in out.splitlines() if x.strip()]
            state: dict[str, list[tuple[str, str]]] = {}
            for row in rows:
                state.setdefault(row.get("Service", "?"), []).append((row.get("State", "?"), row.get("Health", "")))
            with lock:
                containers.clear()
                containers.update(state)
        except Exception:
            pass
        stop.wait(3)


def probe(client: httpx2.Client, path: str) -> tuple[str, int, float]:
    start = time.monotonic()
    try:
        r = client.get(path)
        elapsed = time.monotonic() - start
        if r.status_code in (502, 504):
            return "DOWN", r.status_code, elapsed
        return "UP", r.status_code, elapsed
    except Exception:
        return "DOWN", 0, time.monotonic() - start


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]


def render(probes: dict[str, tuple[str, int, float]]) -> str:
    with lock:
        recent = list(catalogue)
        compose = {k: list(v) for k, v in containers.items()}
    lines = [f"{BOLD}E-Commerce UCE · monitor en vivo{RESET}   gateway {GATEWAY}   {time.strftime('%H:%M:%S')}", ""]

    lines.append(f"{BOLD}Servicios (a través del gateway){RESET}")
    for name, (state, code, seconds) in probes.items():
        color = GREEN if state == "UP" else RED
        lines.append(f"  {name:22} {color}{state:5}{RESET} HTTP {code:3}  {seconds * 1000:6.0f} ms")

    lines.append("")
    lines.append(f"{BOLD}Reparto del catálogo entre réplicas de product-service{RESET} (últimas {len(recent)} peticiones)")
    counts = Counter(instance for _, instance, status, _ in recent if status == 200 and instance)
    total = sum(counts.values()) or 1
    if not counts:
        lines.append(f"  {RED}sin respuestas de product-service{RESET}")
    for instance, n in sorted(counts.items()):
        bar = "█" * max(1, round(40 * n / total))
        lines.append(f"  {instance[:14]:14} {GREEN}{bar}{RESET} {n}")

    window = [e for e in recent if e[0] >= time.time() - 10]
    ok = [s for _, _, status, s in window if status == 200]
    errors = len(window) - len(ok)
    lines.append("")
    lines.append(f"{BOLD}Catálogo, últimos 10 s{RESET}: {len(window) / 10:.1f} pet/s   "
                 f"errores {RED if errors else GREEN}{errors}{RESET}   "
                 f"p50 {percentile(ok, 0.5) * 1000:.0f} ms   p95 {percentile(ok, 0.95) * 1000:.0f} ms")

    lines.append("")
    lines.append(f"{BOLD}Contenedores (docker compose ps){RESET}")
    if not compose:
        lines.append(f"  {DIM}no disponible{RESET}")
    for service in COMPOSE_SERVICES if compose else []:
        rows = compose.get(service, [])
        running = sum(1 for state, _ in rows if state == "running")
        healthy = sum(1 for _, health in rows if health == "healthy")
        color = GREEN if running and healthy == running else (YELLOW if running else RED)
        lines.append(f"  {service:22} {color}{running} en ejecución, {healthy} healthy{RESET}")
    lines.append("")
    lines.append(f"{DIM}Solo lectura. Ctrl+C para salir.{RESET}")
    return "\n".join(lines)


def main() -> None:
    os.system("")  # enables ANSI colours in the Windows console
    stop = threading.Event()
    for target in (catalogue_traffic, docker_state):
        threading.Thread(target=target, args=(stop,), daemon=True).start()
    try:
        with httpx2.Client(base_url=GATEWAY, timeout=5) as client:
            while True:
                probes = {name: probe(client, path) for name, path in PROBES.items()}
                print("\x1b[H\x1b[J" + render(probes), flush=True)
                time.sleep(1)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
