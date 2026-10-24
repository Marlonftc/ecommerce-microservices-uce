"""Isolated fixtures test interpretation; no test stops containers or runs k6."""
import argparse
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from monitor import render
from monitor_core import (Collector, Container, PROBES, Probe, Transitions, classify, distribution,
                          docker_status, effective_probes, gateway_url, http_probe, latency_stats,
                          parse_docker, percentile, read_docker)
from monitor_history import comparison_reason, load_runs, parse_run, throughput_change


def sample(service="product-service", state="UP", code=200, ms=10, at=None, instance="replica-a"):
    return Probe(service, time.monotonic() if at is None else at, time.time(), state, code, ms, instance)


def fixture_run(replicas=1):
    run_id = f"fixture-r{replicas}"
    summary = {
        "run_id": run_id, "scenario": "scale", "generated_at": "2026-10-22T05:00:00Z",
        "scale_config": dict(sustained_rate_it_s=40, max_vus=40, warmup_s=20, sustained_s=60, ramp_up_s=60, hold_s=30),
        "metrics": {
            "http_reqs": {"values": {"count": 10000, "rate": 58.8}},
            "http_req_duration": {"values": {"avg": 20, "med": 15, "p(95)": 40}},
            "http_req_failed": {"values": {"rate": .01}},
            "checks": {"values": {"rate": .99}},
            "http_reqs{phase:ramp}": {"values": {"count": 9000 * replicas, "rate": 999}},
            "http_req_duration{phase:ramp}": {"values": {"avg": 30, "med": 25, "p(95)": 50}, "thresholds": {"p(95)<500": {"ok": True}}},
            "http_req_failed{phase:ramp}": {"values": {"rate": 0}},
        },
    }
    context = {"run_id": run_id, "scenario": "scale", "product_service_replicas": replicas, "k6_exit_code": 0,
               "environment": {"docker_server": "fixture", "os": "fixture", "cpus": 4, "memory_gib": 8},
               "extra_env": ["DO-NOT-DISPLAY"]}
    return summary, context


class ResponseTests(unittest.TestCase):
    def test_expected_and_unexpected_codes(self):
        for service, code, expected in (("product-service", 200, "UP"), ("user-service", 401, "UP"),
                                         ("order-service", 401, "UP"), ("product-service", 401, "DEGRADED"),
                                         ("inventory-service", 404, "UP"), ("product-service", 404, "DEGRADED"),
                                         ("product-service", 500, "DEGRADED"), ("product-service", 503, "DEGRADED"),
                                         ("product-service", 502, "DOWN"), ("product-service", 504, "DOWN"),
                                         ("gateway", None, "DOWN"), ("frontend", 302, "DEGRADED")):
            with self.subTest(service=service, code=code):
                self.assertEqual(classify(service, code)[0], expected)

    def test_gateway_content_and_no_redirects(self):
        class Response(io.BytesIO):
            code = 200
            headers = Message()
        response = Response(b'{"status":"wrong"}')
        response.headers["Content-Type"] = "application/json"
        with patch("monitor_core.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            result = http_probe("http://127.0.0.1:8090", "gateway", "/health", 1)
        self.assertEqual(result.state, "DEGRADED")
        self.assertIsNotNone(result.ms)

    def test_http_error_is_an_observed_response(self):
        headers = Message()
        headers["X-Instance"] = "fixture-id"
        error = urllib.error.HTTPError("http://127.0.0.1", 401, "Unauthorized", headers, io.BytesIO(b"{}"))
        with patch("monitor_core.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            result = http_probe("http://127.0.0.1:8090", "user-service", "/api/users/me", 1)
        self.assertEqual((result.state, result.code, result.instance), ("UP", 401, "fixture-id"))

    def test_timeout_has_no_latency_or_exception_details(self):
        with patch("monitor_core.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = TimeoutError("DO-NOT-DISPLAY")
            result = http_probe("http://127.0.0.1:8090", "gateway", "/health", .2)
        self.assertEqual(result.state, "DOWN")
        self.assertIsNone(result.ms)
        self.assertNotIn("DO-NOT-DISPLAY", repr(result))

    def test_local_gateway_validation(self):
        self.assertEqual(gateway_url("http://127.0.0.1:8090/"), "http://127.0.0.1:8090")
        for value in ("http://example.com", "http://user:secret@localhost", "http://localhost/api", "http://localhost:invalid"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                gateway_url(value)

    def test_stale_and_paused_measurements_are_unknown(self):
        data = {"gateway": sample("gateway", at=10)}
        self.assertEqual(effective_probes(data, 19, 8)["gateway"].state, "UNKNOWN")
        result = effective_probes(data, 11, 8, paused=True)["gateway"]
        self.assertEqual(result.state, "UNKNOWN")
        self.assertEqual(result.code, 200)

    def test_gateway_outage_does_not_claim_backend_outages(self):
        data = {"gateway": sample("gateway", "DOWN", None, at=10),
                "order-service": sample("order-service", "DOWN", None, at=10)}
        result = effective_probes(data, 11, 8)
        self.assertEqual(result["gateway"].state, "DOWN")
        self.assertEqual(result["order-service"].state, "UNKNOWN")
        data["gateway"] = sample("gateway", at=10)
        self.assertEqual(effective_probes(data, 11, 8)["order-service"].state, "DOWN")


class MetricsTests(unittest.TestCase):
    def test_percentiles_use_interpolation_and_empty_is_not_zero(self):
        self.assertIsNone(percentile([], .95))
        self.assertEqual(percentile([0, 10, 20], .5), 10)
        self.assertEqual(percentile([0, 10, 20], .95), 19)
        self.assertEqual(percentile([0], .95), 0)

    def test_latency_window_excludes_failures_old_and_future_samples(self):
        samples = [sample(ms=10, at=99), sample(ms=30, at=98), sample(state="DEGRADED", code=500, ms=200, at=99),
                   sample(ms=999, at=1), sample(ms=999, at=101)]
        self.assertEqual(latency_stats(samples, 100), (2, 20, 29))
        self.assertEqual(latency_stats(samples, 300), (0, None, None))

    def test_distribution_expires_and_excludes_stopped_replicas(self):
        samples = [sample(at=99), sample(at=89), sample(code=500, at=99), sample(at=99, instance="replica-b")]
        self.assertEqual(distribution(samples, 100), {"replica-a": 1, "replica-b": 1})
        self.assertEqual(distribution(samples, 100, {"replica-a"}), {"replica-a": 1})
        self.assertEqual(distribution(samples, 111), {})

    def test_unknown_does_not_close_incident_or_invent_a_failure(self):
        tracker = Transitions()
        for at, state in ((1, "UP"), (2, "DOWN"), (4, "UNKNOWN"), (10, "DEGRADED"), (12, "UP")):
            tracker.observe("HTTP", "gateway", state, at, 100 + at)
        self.assertEqual(tracker.events[-1].duration, 10)
        self.assertEqual(len(tracker.events), 4)
        tracker.observe("HTTP", "gateway", "UP", 13, 113)
        self.assertEqual(len(tracker.events), 4)

    def test_initial_up_has_no_incident_and_events_are_bounded(self):
        tracker = Transitions()
        tracker.observe("HTTP", "gateway", "UP", 0, 0)
        self.assertFalse(tracker.events)
        for at in range(500):
            tracker.observe("HTTP", "gateway", "DOWN" if at % 2 else "UP", at, at)
        self.assertLessEqual(len(tracker.events), 200)


class DockerTests(unittest.TestCase):
    row = {"id": "abcdefghijkl1234", "name": "fixture-product-1", "service": "product-service", "state": "running", "status": "Up 1 minute (healthy)"}

    def test_json_lines_and_array(self):
        first = parse_docker(json.dumps(self.row))[0]
        self.assertEqual(first.identity, "abcdefghijkl")
        self.assertEqual(first.health, "healthy")
        self.assertEqual(parse_docker(json.dumps([self.row])), (first,))
        self.assertEqual(parse_docker(""), ())
        with self.assertRaises(ValueError):
            parse_docker('{"service":null}')

    def test_docker_health_is_separate_from_running(self):
        healthy = Container("a", "a", "product-service", "running", "healthy")
        unhealthy = Container("b", "b", "product-service", "running", "unhealthy")
        stopped = Container("c", "c", "product-service", "exited", "unknown")
        self.assertEqual(docker_status((healthy,), "product-service", True)[0], "UP")
        self.assertEqual(docker_status((healthy, stopped), "product-service", True)[0], "DEGRADED")
        self.assertEqual(docker_status((unhealthy,), "product-service", True)[0], "DEGRADED")
        self.assertEqual(docker_status((stopped,), "product-service", True)[0], "DOWN")
        self.assertEqual(docker_status((healthy,), "product-service", False)[0], "UNKNOWN")

    def test_docker_command_never_uses_compose_or_inspect(self):
        with patch("monitor_core.subprocess.run") as run:
            run.return_value.stdout = json.dumps(self.row)
            read_docker("fixture", 1)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["docker", "ps"])
        self.assertNotIn("compose", command)
        self.assertNotIn("inspect", command)
        self.assertNotIn(".Env", command[-1])
        self.assertEqual(run.call_args.kwargs["timeout"], 1)


class CollectorTests(unittest.TestCase):
    def test_pause_discards_inflight_batch_and_clears_traffic(self):
        collector = Collector("http://localhost")
        collector.traffic.append(sample())
        collector.set_paused(True)
        collector.apply_probes([sample()], 0)
        self.assertFalse(collector.probes)
        self.assertFalse(collector.snapshot()["traffic"])
        collector.set_paused(False)
        collector.apply_probes([sample()], collector.generation)
        self.assertIn("product-service", collector.probes)

    def test_rate_change_drops_old_distribution(self):
        collector = Collector("http://localhost")
        collector.traffic.append(sample())
        collector.set_traffic(.2)
        self.assertFalse(collector.snapshot()["counts"])
        for rate in (-1, 6, float("nan")):
            with self.assertRaises(ValueError):
                collector.set_traffic(rate)

    def test_docker_failure_retains_data_as_unknown(self):
        collector = Collector("http://localhost")
        collector.apply_docker((Container("id", "fixture", "gateway", "running", "healthy"),), time.monotonic())
        collector.docker_error = "unavailable"
        data = collector.snapshot()
        self.assertFalse(data["docker_available"])
        self.assertEqual(len(data["containers"]), 1)

    def test_http_batches_do_not_overlap_and_threads_stop(self):
        active, maximum, calls = 0, 0, []
        lock = threading.Lock()
        def probe(gateway, service, path, timeout):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(.03)
            with lock:
                active -= 1
                calls.append(service)
            return sample(service=service)
        collector = Collector("http://localhost", interval=.01, probe_fn=probe, docker_fn=lambda *_: (), rate=0)
        collector.start()
        time.sleep(.18)
        collector.stop()
        self.assertTrue(collector.join(2))
        self.assertLessEqual(maximum, len(PROBES))
        self.assertTrue(calls)
        self.assertFalse(collector.traffic)

    def test_console_uses_shared_snapshot(self):
        collector = Collector("http://localhost")
        collector.apply_probes([sample("gateway"), sample()], 0)
        text = render(collector.snapshot(), collector.gateway)
        self.assertIn("product-service", text)
        self.assertIn("UNKNOWN", text)
        self.assertIn("PAUSADO", text)
        self.assertIn("no lee .env", text)


class HistoryTests(unittest.TestCase):
    def test_units_thresholds_and_recorded_phase_duration(self):
        summary, context = fixture_run()
        run = parse_run(summary, context)
        total, ramp = run.metrics
        self.assertEqual(total.p95, 40)
        self.assertEqual(total.errors, 1)
        self.assertEqual(run.checks, 99)
        self.assertEqual(ramp.rps, 100)
        self.assertNotEqual(ramp.rps, 999)
        self.assertTrue(run.thresholds[0].ok)
        self.assertNotIn("DO-NOT-DISPLAY", repr(run))

    def test_context_must_match_run_id_and_scenario(self):
        summary, context = fixture_run()
        for key in ("run_id", "scenario"):
            wrong = {**context, key: "wrong"}
            run = parse_run(summary, wrong)
            self.assertIsNone(run.replicas)
            self.assertIn("rechazado", run.context_note)

    def test_missing_values_remain_unknown(self):
        summary, _ = fixture_run()
        summary["metrics"] = {"http_req_duration": {"values": {"avg": float("nan")}}}
        run = parse_run(summary)
        self.assertIsNone(run.replicas)
        self.assertIsNone(run.metrics[0].avg)
        self.assertIsNone(run.metrics[0].rps)
        self.assertIsNone(run.metrics[0].errors)
        self.assertFalse(run.thresholds)

    def test_comparison_uses_matched_configuration(self):
        one = parse_run(*fixture_run(1))
        three = parse_run(*fixture_run(3))
        self.assertEqual(comparison_reason(one, three), "")
        self.assertEqual(throughput_change(one.metrics[1], three.metrics[1]), 200)
        summary, context = fixture_run(3)
        summary["scale_config"]["max_vus"] = 80
        self.assertIn("difieren", comparison_reason(one, parse_run(summary, context)))

    def test_loader_retries_partial_files_without_misassociation(self):
        summary, context = fixture_run()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory)
            (path / "fixture-r1.json").write_text(json.dumps(summary), encoding="utf-8")
            (path / "fixture-r1-env.json").write_text('{"run_id":', encoding="utf-8")
            (path / "partial.json").write_text("{", encoding="utf-8")
            runs, issues = load_runs(path)
            self.assertEqual(len(runs), 1)
            self.assertIsNone(runs[0].replicas)
            self.assertTrue(issues)
            (path / "fixture-r1-env.json").write_text(json.dumps(context), encoding="utf-8")
            self.assertEqual(load_runs(path)[0][0].replicas, 1)

    def test_loader_rejects_filename_mismatch(self):
        summary, _ = fixture_run()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory)
            (path / "different.json").write_text(json.dumps(summary), encoding="utf-8")
            self.assertFalse(load_runs(path)[0])

    def test_existing_real_reports_parse_without_writes(self):
        runs, issues = load_runs()
        self.assertGreaterEqual(len(runs), 9)
        self.assertFalse(issues)
        self.assertTrue(all(r.date.tzinfo is not None for r in runs))
        self.assertTrue(any(r.replicas == 1 for r in runs))
        self.assertTrue(any(r.replicas == 3 for r in runs))


if __name__ == "__main__":
    unittest.main()
