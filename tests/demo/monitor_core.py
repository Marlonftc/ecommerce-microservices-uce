"""Read-only collectors and bounded in-memory measurements for both monitor views."""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("gateway", "frontend", "user-service", "product-service", "inventory-service",
            "order-service", "payment-service", "notification-service", "order-worker", "postgres")
PROBES = {"gateway": "/health", "frontend": "/", "user-service": "/api/users/me",
          "product-service": "/api/products?limit=1", "inventory-service": "/api/inventory/1",
          "order-service": "/api/cart", "payment-service": "/api/payments",
          "notification-service": "/api/notifications"}
PROTECTED = {"user-service", "order-service", "payment-service", "notification-service"}
STATES = ("UP", "DOWN", "DEGRADED", "UNKNOWN")


def number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def percentile(values: list[float], fraction: float) -> float | None:
    """Linear interpolation between adjacent ranks; an empty sample is not zero."""
    if not 0 <= fraction <= 1:
        raise ValueError("percentile must be between 0 and 1")
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    lo, hi = math.floor(rank), math.ceil(rank)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


@dataclass(frozen=True)
class Probe:
    service: str
    at: float
    wall: float
    state: str
    code: int | None = None
    ms: float | None = None
    instance: str | None = None
    note: str = ""


def classify(service: str, code: int | None) -> tuple[str, str]:
    if code is None or code in (502, 504):
        return "DOWN", "Sin respuesta HTTP" if code is None else "Upstream no disponible"
    if code == 401 and service in PROTECTED:
        return "UP", "Ruta accesible; 401 esperado, no valida la operación ni la base"
    if code == 200:
        return "UP", "Respuesta esperada"
    if service == "inventory-service" and code == 404:
        return "UP", "Ruta accesible; no existe stock para el producto de la sonda"
    return "DEGRADED", "Respuesta HTTP inesperada"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_probe(gateway: str, service: str, path: str, timeout: float) -> Probe:
    start = time.monotonic()
    code, instance, content_type, body = None, None, "", b""
    note = ""
    try:
        # Ignore system proxies and never send credentials or follow redirects.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(gateway + path, headers={"User-Agent": "UCE-demo-monitor/1"})
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            code = response.code
            candidate = response.headers.get("X-Instance", "")
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", candidate):
                instance = candidate
            content_type = response.headers.get_content_type()
            body = response.read(65536)
    except (TimeoutError, socket.timeout):
        note = "Tiempo de espera agotado"
        code = None
    except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException):
        note = "Conexión no disponible"
        code = None
    state, explanation = classify(service, code)
    if code == 200 and service == "gateway":
        try:
            valid = content_type == "application/json" and json.loads(body).get("status") == "ok"
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            state, explanation = "DEGRADED", "El contenido no corresponde a /health del Gateway"
    if code == 200 and service == "frontend" and content_type != "text/html":
        state, explanation = "DEGRADED", "Se esperaba HTML del frontend"
    end = time.monotonic()
    return Probe(service, end, time.time(), state, code,
                 (end - start) * 1000 if code is not None else None, instance, note or explanation)


def effective_probes(probes: dict[str, Probe], now: float, stale_after: float,
                     paused: bool = False) -> dict[str, Probe]:
    result = {}
    for service in PROBES:
        sample = probes.get(service, Probe(service, now, time.time(), "UNKNOWN", note="Sin medición"))
        if paused or now - sample.at > stale_after:
            sample = replace(sample, state="UNKNOWN", note="Sondas pausadas" if paused else "Medición desactualizada")
        result[service] = sample
    if result["gateway"].state in ("DOWN", "UNKNOWN"):
        for service, sample in result.items():
            if service != "gateway" and sample.state == "DOWN":
                result[service] = replace(sample, state="UNKNOWN", note="Sin visibilidad a través del Gateway")
    return result


@dataclass(frozen=True)
class Container:
    identity: str
    name: str
    service: str
    state: str
    health: str


def parse_docker(text: str) -> tuple[Container, ...]:
    rows = json.loads(text) if text.lstrip().startswith("[") else [json.loads(s) for s in text.splitlines() if s.strip()]
    result = []
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("id", "name", "service", "state", "status")):
            raise ValueError("invalid docker state")
        if row["service"] not in SERVICES:
            continue
        match = re.search(r"\((healthy|unhealthy|health: starting)\)", row["status"])
        result.append(Container(row["id"][:12], row["name"], row["service"], row["state"],
                                match[1].replace("health: ", "") if match else "unknown"))
    return tuple(result)


def read_docker(project: str, timeout: float) -> tuple[Container, ...]:
    # Select fields only: no compose interpolation, environment, logs or secrets.
    template = ('{"id":{{json .ID}},"name":{{json .Names}},'
                '"service":{{json (.Label "com.docker.compose.service")}},'
                '"state":{{json .State}},"status":{{json .Status}}}')
    result = subprocess.run(["docker", "ps", "-a", "--no-trunc", "--filter",
                             f"label=com.docker.compose.project={project}", "--format", template],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return parse_docker(result.stdout)


def docker_status(rows: tuple[Container, ...], service: str, available: bool) -> tuple[str, str]:
    if not available:
        return "UNKNOWN", "Sin estado Docker vigente"
    group = [row for row in rows if row.service == service]
    running = sum(c.state == "running" for c in group)
    healthy = sum(c.state == "running" and c.health == "healthy" for c in group)
    if not running:
        return "DOWN", "Sin contenedores en ejecución"
    state = "UP" if healthy == running and running == len(group) else "DEGRADED"
    return state, f"{running}/{len(group)} running · {healthy} healthy"


@dataclass(frozen=True)
class Transition:
    wall: float
    source: str
    service: str
    before: str
    after: str
    duration: float | None


class Transitions:
    def __init__(self):
        self.previous: dict[tuple[str, str], str] = {}
        self.incidents: dict[tuple[str, str], float] = {}
        self.events: deque[Transition] = deque(maxlen=200)

    def observe(self, source: str, service: str, state: str, at: float, wall: float) -> None:
        key = (source, service)
        before = self.previous.get(key, "UNKNOWN")
        self.previous[key] = state
        if state == before:
            return
        duration = None
        if state in ("DOWN", "DEGRADED"):
            self.incidents.setdefault(key, at)
        elif state == "UP" and key in self.incidents:
            duration = max(0, at - self.incidents.pop(key))
        if before != "UNKNOWN" or state in ("DOWN", "DEGRADED") or duration is not None:
            self.events.append(Transition(wall, source, service, before, state, duration))


def latency_stats(samples: list[Probe], now: float, window: float = 60) -> tuple[int, float | None, float | None]:
    values = [p.ms for p in samples if 0 <= now - p.at <= window and p.state == "UP" and p.ms is not None]
    return len(values), percentile(values, .5), percentile(values, .95)


def distribution(samples: list[Probe], now: float, active: set[str] | None = None) -> Counter:
    return Counter(p.instance for p in samples if 0 <= now - p.at <= 10 and p.code == 200 and p.instance
                   and (active is None or p.instance in active))


class Collector:
    def __init__(self, gateway: str, project: str = "ecommerce-uce", interval: float = 3,
                 timeout: float = 2, docker_interval: float = 5, rate: float = 0,
                 probe_fn=http_probe, docker_fn=read_docker):
        self.gateway, self.project = gateway, project
        self.interval, self.timeout, self.docker_interval = interval, timeout, docker_interval
        self.stale_after = max(8, interval * 2 + timeout)
        self.probe_fn, self.docker_fn = probe_fn, docker_fn
        self.lock = threading.RLock()
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.threads: list[threading.Thread] = []
        self.probes: dict[str, Probe] = {}
        self.samples = {s: deque(maxlen=600) for s in PROBES}
        self.chart = {s: deque(maxlen=600) for s in PROBES}
        self.traffic: deque[Probe] = deque(maxlen=600)
        self.seen: dict[str, float] = {}
        self.transitions = Transitions()
        self.containers: tuple[Container, ...] = ()
        self.docker_at: float | None = None
        self.docker_error = "Esperando Docker"
        self.rate, self.paused, self.generation = rate, False, 0
        self.started = time.monotonic()

    def set_traffic(self, rate: float) -> None:
        if not math.isfinite(rate) or not 0 <= rate <= 5:
            raise ValueError("rate must be between 0 and 5")
        with self.lock:
            self.rate = rate
            self.generation += 1
            self.traffic.clear()
        self.wake.set()

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused
            self.generation += 1
            self.traffic.clear()
        self.wake.set()

    def apply_probes(self, batch: list[Probe], generation: int) -> None:
        with self.lock:
            if self.paused or generation != self.generation or self.stop_event.is_set():
                return
            for p in batch:
                self.probes[p.service] = p
                self.samples[p.service].append(p)
                if p.service == "product-service" and p.instance and p.code == 200:
                    self.seen[p.instance] = p.at
            now = time.monotonic()
            effective = effective_probes(self.probes, now, self.stale_after)
            for p in batch:
                measured = effective[p.service]
                _, p50, p95 = latency_stats(list(self.samples[p.service]), p.at)
                self.chart[p.service].append((p.at, p50 if measured.state == "UP" else None,
                                              p95 if measured.state == "UP" else None))
                self.transitions.observe("HTTP", p.service, measured.state, p.at, p.wall)

    def apply_docker(self, rows: tuple[Container, ...], now: float) -> None:
        with self.lock:
            self.containers, self.docker_at, self.docker_error = rows, now, ""
            for service in SERVICES:
                state, _ = docker_status(rows, service, True)
                self.transitions.observe("Docker", service, state, now, time.time())

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self.lock:
            self.seen = {k: v for k, v in self.seen.items() if now - v <= 300}
            available = self.docker_at is not None and not self.docker_error and now - self.docker_at <= max(12, self.docker_interval * 2 + self.timeout)
            active = {c.identity for c in self.containers if c.service == "product-service" and c.state == "running"} if available else None
            return dict(now=now, probes=effective_probes(self.probes, now, self.stale_after, self.paused),
                        raw=dict(self.probes), stats={s: latency_stats(list(v), now) for s, v in self.samples.items()},
                        chart={s: list(v) for s, v in self.chart.items()}, containers=self.containers,
                        docker_available=bool(available), docker_at=self.docker_at, docker_error=self.docker_error,
                        events=list(self.transitions.events), counts=distribution(list(self.traffic), now, active),
                        traffic=[p for p in self.traffic if 0 <= now - p.at <= 10], seen=dict(self.seen),
                        rate=self.rate, paused=self.paused, elapsed=now - self.started)

    def _http_loop(self) -> None:
        # Exactly one batch can be in flight; no queue of polling cycles accumulates.
        with ThreadPoolExecutor(max_workers=len(PROBES), thread_name_prefix="probe") as pool:
            while not self.stop_event.is_set():
                with self.lock:
                    paused, generation = self.paused, self.generation
                if not paused:
                    futures = [pool.submit(self.probe_fn, self.gateway, name, path, self.timeout)
                               for name, path in PROBES.items()]
                    batch = [future.result() for future in futures]
                    self.apply_probes(batch, generation)
                self.stop_event.wait(self.interval)

    def _docker_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.apply_docker(self.docker_fn(self.project, self.timeout + 1), time.monotonic())
            except (OSError, subprocess.SubprocessError, ValueError, TypeError):
                with self.lock:
                    self.docker_error = "Docker no disponible; última lectura conservada como UNKNOWN"
            self.stop_event.wait(self.docker_interval)

    def _traffic_loop(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                rate, paused, generation = self.rate, self.paused, self.generation
            if not paused and rate > 0:
                sample = self.probe_fn(self.gateway, "product-service", PROBES["product-service"], self.timeout)
                with self.lock:
                    if generation == self.generation and not self.paused and not self.stop_event.is_set():
                        self.traffic.append(sample)
                        if sample.code == 200 and sample.instance:
                            self.seen[sample.instance] = sample.at
            self.wake.wait(1 / rate if rate > 0 and not paused else .5)
            self.wake.clear()

    def start(self) -> None:
        if self.threads:
            raise RuntimeError("collector already started")
        for target in (self._http_loop, self._docker_loop, self._traffic_loop):
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()

    def join(self, timeout: float = 4) -> bool:
        deadline = time.monotonic() + timeout
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in self.threads)


def gateway_url(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or parts.hostname not in ("127.0.0.1", "localhost", "::1")
            or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/")):
        raise argparse.ArgumentTypeError("use a local Gateway URL without credentials, e.g. http://127.0.0.1:8090")
    try:
        _ = parts.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid Gateway port") from exc
    return value.rstrip("/")


def arguments(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--gateway", type=gateway_url,
                        default=f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT') or '8080'}")
    parser.add_argument("--project", default="ecommerce-uce", help="Docker Compose project label")
    parser.add_argument("--interval", type=float, default=3, help="seconds between completed probe batches (>= 1)")
    parser.add_argument("--timeout", type=float, default=2, help="HTTP socket timeout in seconds (0.2..10)")
    parser.add_argument("--traffic-rate", type=float, default=0, help="extra catalogue requests/s, 0..5; default paused")
    return parser


def validate_args(parser: argparse.ArgumentParser, args) -> None:
    if not math.isfinite(args.interval) or args.interval < 1:
        parser.error("--interval must be finite and >= 1")
    if not math.isfinite(args.timeout) or not .2 <= args.timeout <= 10:
        parser.error("--timeout must be between 0.2 and 10 seconds")
    if not math.isfinite(args.traffic_rate) or not 0 <= args.traffic_rate <= 5:
        parser.error("--traffic-rate must be between 0 and 5")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", args.project):
        parser.error("invalid project label")
