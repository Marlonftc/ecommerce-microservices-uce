"""Read-only terminal view of the shared demonstration monitor.

Example:
    python tests/demo/monitor.py --gateway http://127.0.0.1:8090
    Ctrl+C exits. Extra synthetic traffic is paused by default.
"""
import os
import sys
import time

from monitor_core import Collector, SERVICES, arguments, docker_status, validate_args

COLORS = {"UP": "\x1b[32m", "DOWN": "\x1b[31m", "DEGRADED": "\x1b[33m", "UNKNOWN": "\x1b[90m"}
RESET = "\x1b[0m"


def value(number):
    return "—" if number is None else f"{number:.1f}"


def render(data: dict, gateway: str, color: bool = True) -> str:
    lines = [f"E-Commerce UCE · monitor local · {gateway} · {time.strftime('%H:%M:%S')}",
             "HTTP y Docker son evidencias independientes. 401 esperado = ruta accesible, no salud completa.",
             "", "Componente             HTTP       Docker       Código   ms       p50/60s  p95/60s  Edad"]
    for name in SERVICES:
        probe = data["probes"].get(name)
        raw = data["raw"].get(name)
        docker, _ = docker_status(data["containers"], name, data["docker_available"])
        state = probe.state if probe else docker
        measured = probe is not None and probe.state != "UNKNOWN"
        _, p50, p95 = data["stats"].get(name, (0, None, None))
        elapsed = f"{data['now'] - raw.at:.0f}s" if raw else "—"
        tint, reset = (COLORS[state], RESET) if color else ("", "")
        lines.append(f"{name:22} {tint}{probe.state if probe else '—':10}{reset} {docker:12} "
                     f"{str(probe.code) if probe and probe.code is not None else '—':6} "
                     f"{value(probe.ms if measured else None):8} {value(p50 if measured else None):8} "
                     f"{value(p95 if measured else None):8} {elapsed}")
    lines += ["", "Distribución sintética extra · últimos 10 s · NO es el tráfico total del sistema"]
    if data["paused"] or not data["rate"]:
        lines.append("PAUSADO (las sondas básicas siguen activas salvo pausa de todo HTTP)")
    else:
        for instance, count in sorted(data["counts"].items()):
            lines.append(f"  {instance:16} {'█' * min(count, 40)} {count}")
        if not data["counts"]:
            lines.append("Sin respuestas recientes con X-Instance")
        lines.append(f"{len(data['traffic'])} peticiones extra / 10 s · objetivo máximo {data['rate']:g} req/s")
    lines += ["", data["docker_error"] or "Docker: lectura vigente" if data["docker_available"] else
              "Docker: UNKNOWN / lectura ausente o desactualizada", "", "Últimas transiciones (duración desde detección):"]
    for event in data["events"][-5:]:
        recovery = "" if event.duration is None else f" · ~{event.duration:.0f} s"
        lines.append(f"  {time.strftime('%d/%m %H:%M:%S', time.localtime(event.wall))} "
                     f"{event.source} {event.service}: {event.before} → {event.after}{recovery}")
    lines.append("\nSolo lectura · no lee .env · Ctrl+C para salir")
    return "\n".join(lines)


def main():
    parser = arguments("Monitor UCE de consola: HTTP y Docker, sin leer .env")
    parser.add_argument("--once", action="store_true", help="one bounded live snapshot, then exit")
    args = parser.parse_args()
    validate_args(parser, args)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.name == "nt":
        os.system("")  # Enable ANSI colors on the Windows console.
    collector = Collector(args.gateway, args.project, args.interval, args.timeout, rate=args.traffic_rate)
    collector.start()
    try:
        if args.once:
            collector.stop_event.wait(args.timeout + 1.5)
            print(render(collector.snapshot(), args.gateway, sys.stdout.isatty()), flush=True)
        else:
            while True:
                print("\x1b[H\x1b[J" + render(collector.snapshot(), args.gateway), flush=True)
                collector.stop_event.wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        collector.join(args.timeout + 2)


if __name__ == "__main__":
    main()
