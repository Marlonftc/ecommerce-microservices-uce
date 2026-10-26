"""Read existing k6 summaries; never execute scenarios or expose raw context fields."""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from monitor_core import ROOT, number

RESULTS = ROOT / "tests" / "load" / "results"


def mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def positive(value: object) -> float | None:
    value = number(value)
    return value if value is not None and value >= 0 else None


@dataclass(frozen=True)
class Metric:
    phase: str
    requests: float | None
    rps: float | None
    avg: float | None
    p50: float | None
    p95: float | None
    errors: float | None


@dataclass(frozen=True)
class Threshold:
    metric: str
    expression: str
    ok: bool | None


@dataclass(frozen=True)
class Run:
    run_id: str
    scenario: str
    date: datetime
    replicas: int | None
    metrics: tuple[Metric, ...]
    thresholds: tuple[Threshold, ...]
    exit_code: int | None
    config: tuple
    environment: tuple
    checks: float | None
    context_note: str


def metric_values(metrics: dict, key: str) -> dict:
    return mapping(mapping(metrics.get(key)).get("values"))


def parse_run(summary: dict, context: dict | None = None) -> Run:
    run_id = summary.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", run_id):
        raise ValueError("invalid run_id")
    if summary.get("scenario") not in ("browse", "purchase", "mixed", "scale"):
        raise ValueError("unknown scenario")
    date = datetime.fromisoformat(summary["generated_at"].replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("timestamp must include its timezone")
    if not isinstance(summary.get("metrics"), dict):
        raise ValueError("missing k6 metrics")
    note = ""
    if context is None:
        note = "Contexto pendiente o no disponible"
    elif context.get("run_id") != run_id or context.get("scenario") != summary["scenario"]:
        context, note = None, "Contexto rechazado: run_id o escenario no coincide"
    context = context or {}
    replicas = context.get("product_service_replicas")
    replicas = replicas if type(replicas) is int and replicas > 0 else None
    exit_code = context.get("k6_exit_code")
    exit_code = exit_code if type(exit_code) is int else None
    metrics, config = summary["metrics"], mapping(summary.get("scale_config"))
    rows = []
    for phase in ("total", "main", "sustained", "ramp"):
        suffix = "" if phase == "total" else "{phase:" + phase + "}"
        duration = metric_values(metrics, "http_req_duration" + suffix)
        requests = metric_values(metrics, "http_reqs" + suffix)
        errors = metric_values(metrics, "http_req_failed" + suffix)
        if not any((duration, requests, errors)):
            continue
        count = positive(requests.get("count"))
        rps = positive(requests.get("rate")) if phase == "total" else None
        seconds = None
        if phase == "sustained":
            seconds = positive(config.get("sustained_s"))
        elif phase == "ramp":
            up, hold = positive(config.get("ramp_up_s")), positive(config.get("hold_s"))
            seconds = up + hold if up is not None and hold is not None else None
        if seconds and count is not None:
            # k6 submetric rates use the total run duration: divide phase counts by their recorded duration.
            rps = count / seconds
        error_rate = positive(errors.get("rate"))
        rows.append(Metric(phase, count, rps, positive(duration.get("avg")), positive(duration.get("med")),
                           positive(duration.get("p(95)")), error_rate * 100 if error_rate is not None and error_rate <= 1 else None))
    thresholds = []
    for name, value in sorted(metrics.items()):
        for expression, status in mapping(mapping(value).get("thresholds")).items():
            ok = mapping(status).get("ok")
            thresholds.append(Threshold(name, expression, ok if type(ok) is bool else None))
    checks = positive(metric_values(metrics, "checks").get("rate"))
    keys = ("sustained_rate_it_s", "max_vus", "warmup_s", "sustained_s", "ramp_up_s", "hold_s")
    environment = mapping(context.get("environment"))
    return Run(run_id, summary["scenario"], date, replicas, tuple(rows), tuple(thresholds), exit_code,
               tuple(positive(config.get(k)) for k in keys),
               tuple(environment.get(k) for k in ("docker_server", "os", "cpus", "memory_gib")),
               checks * 100 if checks is not None and checks <= 1 else None, note)


def read_object(path: Path, directory: Path) -> dict:
    if path.is_symlink() or path.resolve().parent != directory.resolve() or path.stat().st_size > 5_000_000:
        raise ValueError("unexpected report path or size")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("report must be an object")
    return value


def load_runs(directory: Path = RESULTS) -> tuple[tuple[Run, ...], tuple[str, ...]]:
    runs, issues = [], []
    try:
        files = sorted(directory.glob("*.json"))
        if not directory.is_dir():
            return (), ("Carpeta de resultados no disponible",)
        if len(files) > 1000:
            return (), ("Demasiados archivos de resultados; revise la carpeta",)
    except OSError:
        return (), ("No se pudo consultar la carpeta de resultados",)
    for path in files:
        if path.name.endswith("-env.json"):
            continue
        try:
            data = read_object(path, directory)
            if data.get("run_id") != path.stem:
                raise ValueError("filename and run_id differ")
            context_path = directory / (path.stem + "-env.json")
            context = None
            if context_path.exists():
                try:
                    context = read_object(context_path, directory)
                except (OSError, ValueError, TypeError):
                    issues.append(f"{path.stem}: contexto incompleto o inválido; se reintentará")
            run = parse_run(data, context)
            runs.append(run)
            if run.context_note:
                issues.append(f"{path.stem}: {run.context_note}")
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            issues.append(f"{path.stem}: resumen incompleto o inválido; se reintentará")
    return tuple(sorted(runs, key=lambda run: (run.date, run.run_id), reverse=True)), tuple(issues)


def comparison_reason(one: Run, three: Run) -> str:
    if one.scenario != "scale" or three.scenario != "scale" or (one.replicas, three.replicas) != (1, 3):
        return "Seleccione dos ejecuciones scale: una con 1 réplica y otra con 3"
    if any(value is None for value in (*one.config, *three.config)):
        return "Configuración de carga incompleta: comparación solo descriptiva"
    if one.config != three.config:
        return "Las configuraciones de carga difieren: comparación solo descriptiva"
    if any(value is None for value in (*one.environment, *three.environment)):
        return "Entorno incompleto: comparación solo descriptiva"
    if one.environment != three.environment:
        return "Los entornos registrados difieren: comparación solo descriptiva"
    if one.exit_code != 0 or three.exit_code != 0:
        return "Alguna ejecución no terminó correctamente: comparación solo descriptiva"
    return ""


def throughput_change(one: Metric, three: Metric) -> float | None:
    if one.phase != three.phase or not one.rps or three.rps is None:
        return None
    return (three.rps / one.rps - 1) * 100


class HistoryReader:
    def __init__(self, directory: Path = RESULTS):
        self.directory = directory
        self.lock = threading.Lock()
        self.wake, self.stopped = threading.Event(), threading.Event()
        self.runs: tuple[Run, ...] = ()
        self.issues: tuple[str, ...] = ()
        self.loaded = False
        self.thread = threading.Thread(target=self._loop, daemon=True, name="history-reader")

    def _loop(self) -> None:
        while not self.stopped.is_set():
            self.wake.clear()
            runs, issues = load_runs(self.directory)
            with self.lock:
                self.runs, self.issues, self.loaded = runs, issues, True
            self.wake.wait(10)

    def snapshot(self) -> tuple[tuple[Run, ...], tuple[str, ...], bool]:
        with self.lock:
            return self.runs, self.issues, self.loaded

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.wake.set()
