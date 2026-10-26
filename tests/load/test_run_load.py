"""Isolated runner contracts. No real Docker, HTTP, .env or business data is used."""
import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import k6_observability as obs
import run_load as load


class RunnerTests(unittest.TestCase):
    def test_import_does_not_read_application_env(self):
        original = Path.read_text
        def guarded(path, *args, **kwargs):
            if path.name == ".env":
                raise AssertionError("Application env was read")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", guarded):
            spec = importlib.util.spec_from_file_location("runner_import_fixture", Path(load.__file__))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.assertEqual(module.GATEWAY, "http://127.0.0.1:8080")

    def test_legacy_command_is_unchanged_without_flag(self):
        command = load.k6_command("scale", "fixture-r1", 1, ["MAX_VUS=40"])
        self.assertEqual(command, ["run", "--rm", "-e", "RUN_ID=fixture-r1", "-e", "MAX_VUS=40",
                                   "k6", "run", "--quiet", "/scripts/scale.js"])

    def test_remote_command_has_global_identity_and_bounded_labels(self):
        command = load.k6_command("scale", "fixture-r3", 3, [], True, "fixturekey")
        for value in ("experimental-prometheus-rw", "testid=fixture-r3", "load_scenario=scale",
                      "product_replicas=3", "measurement_kind=benchmark", "comparison_key=fixturekey"):
            self.assertIn(value, command)
        self.assertIn("K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM=true", command)
        self.assertIn("K6_PROMETHEUS_RW_STALE_MARKERS=true", command)
        self.assertNotIn("url", obs.SYSTEM_TAGS.split(","))
        self.assertNotIn("vu", obs.SYSTEM_TAGS.split(","))
        self.assertNotIn("iter", obs.SYSTEM_TAGS.split(","))
        self.assertIn("expected_response", obs.SYSTEM_TAGS.split(","))

    def test_reserved_env_cannot_replace_run_identity_or_exporter(self):
        for entry, remote in (("RUN_ID=other", False), ("RUN_ID=other", True),
                              ("K6_TAGS=testid=other", True), ("K6_OUT=other", True),
                              ("K6_SYSTEM_TAGS=url", True), ("K6_PROMETHEUS_RW_SERVER_URL=http://external", True)):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                obs.validate_extra_env([entry], remote)

    def test_invalid_replica_counts_fail_before_commands(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            load.parse_args(["scale", "--replicas", "0"])

    def test_reservation_preserves_existing_summary_log_and_context(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(load, "RESULTS", Path(folder)):
            for suffix in (".json", ".log", "-env.json"):
                (Path(folder) / ("scale-r1-stamp" + suffix)).write_text("preserve", encoding="utf-8")
            first, lock1 = load.reserve_run_id("scale", 1, "stamp")
            second, lock2 = load.reserve_run_id("scale", 1, "stamp")
            self.assertNotEqual(first, second)
            self.assertNotEqual(first, "scale-r1-stamp")
            for suffix in (".json", ".log", "-env.json"):
                self.assertEqual((Path(folder) / ("scale-r1-stamp" + suffix)).read_text(), "preserve")
            lock1.unlink()
            lock2.unlink()

    def test_exclusive_report_write_never_truncates(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "fixture.json"
            report.write_text("original", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                load.write_new(report, "new")
            self.assertEqual(report.read_text(), "original")

    def test_disabled_receiver_stops_before_scaling_or_k6(self):
        with patch.object(load, "env_value", return_value="8090"), \
                patch.object(load, "remote_preflight", side_effect=RuntimeError("fixture")), \
                patch.object(load, "compose") as compose, patch.object(load, "prepare_replicas") as scale:
            with self.assertRaises(SystemExit):
                load.main(["scale", "--prometheus"])
            compose.assert_not_called()
            scale.assert_not_called()

    def test_preflight_checks_both_real_networks_and_receiver(self):
        replies = [subprocess.CompletedProcess([], 0, stdout=name) for name in (obs.APPLICATION_NETWORK, obs.MONITORING_NETWORK)]
        with patch.object(load, "run", side_effect=replies) as command, \
                patch.object(load, "read_prometheus", return_value={"status": "success", "data": {"web.enable-remote-write-receiver": "true"}}):
            load.remote_preflight()
        self.assertEqual(command.call_count, 2)

    def test_export_observation_requires_actual_native_histograms(self):
        scalar = {"status": "success", "data": {"result": [{"values": [[1, "1"]]}]}}
        histogram = {"status": "success", "data": {"result": [{"histograms": [[1, {"count": "8"}]]}]}}
        with patch.object(load, "read_prometheus", side_effect=[scalar, scalar, histogram]):
            status, _ = load.export_observation("fixture", 0, 20000)
            self.assertEqual(status, "samples_observed")
        with patch.object(load, "read_prometheus", side_effect=[scalar, scalar, scalar]):
            status, _ = load.export_observation("fixture", 0, 20000)
            self.assertEqual(status, "incomplete")
        with patch.object(load, "read_prometheus", side_effect=OSError("fixture")):
            self.assertEqual(load.export_observation("fixture", 0, 20000), ("unverified", {}))

    def run_fixture(self, remote, exit_code):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            directory = Path(folder)
            stack.enter_context(patch.object(load, "RESULTS", directory))
            stack.enter_context(patch.object(load, "env_value", return_value="8090"))
            stack.enter_context(patch.object(load, "get", return_value=(200, {}, .01)))
            stack.enter_context(patch.object(load, "prepare_replicas", return_value=["fixture-replica"]))
            stack.enter_context(patch.object(load, "wait_until_recovered"))
            stack.enter_context(patch.object(load, "replica_ips", return_value={}))
            stack.enter_context(patch.object(load, "docker_environment", return_value={"cpus": 4}))
            stack.enter_context(patch.object(load, "application_images", return_value={"product-service": ["fixture-image"]}))
            preflight = stack.enter_context(patch.object(load, "remote_preflight"))
            stack.enter_context(patch.object(load, "export_observation", return_value=("unverified", {})))
            stack.enter_context(patch.object(load, "gateway_demand", return_value={"total": 8, "per_service_percent": {}, "product_service_replicas": {}}))
            sampler = MagicMock()
            sampler.summary.return_value = {}
            stack.enter_context(patch.object(load, "StatsSampler", return_value=sampler))
            def fake_compose(*args, **kwargs):
                if args[-1] == "version":
                    return subprocess.CompletedProcess([], 0, stdout="k6 fixture-version", stderr="")
                identity = next(a.partition("=")[2] for a in args if a.startswith("RUN_ID="))
                summary = {"run_id": identity, "scenario": "scale", "fixture": True, "metrics": {}}
                destination = directory / ".pending" / identity
                (destination / f"{identity}.json").write_text(json.dumps(summary), encoding="utf-8")
                return subprocess.CompletedProcess([], exit_code, stdout="fixture output  \n", stderr="")
            command = stack.enter_context(patch.object(load, "compose", side_effect=fake_compose))
            with contextlib.redirect_stdout(io.StringIO()):
                load.main(["scale", *(["--prometheus"] if remote else [])])
            contexts = list(directory.glob("*-env.json"))
            self.assertEqual(len(contexts), 1)
            context = json.loads(contexts[0].read_text())
            self.assertEqual(context["k6_exit_code"], exit_code)
            self.assertEqual(context["summary_status"], "verified")
            self.assertEqual(len(list(directory.glob("*.log"))), 1)
            self.assertEqual(len(list(directory.glob("*.lock"))), 0)
            sampler.stop_event.set.assert_called_once()
            sampler.join.assert_called_once()
            self.assertEqual(command.call_args.kwargs["files"], load.PROMETHEUS_FILES if remote else load.LOAD_FILES)
            if remote:
                preflight.assert_called_once()
                self.assertEqual(context["observability"]["export_status"], "unverified")
                self.assertEqual(context["observability"]["labels"]["testid"], context["run_id"])
            else:
                preflight.assert_not_called()
                self.assertNotIn("observability", context)

    def test_main_preserves_legacy_reports_and_k6_exit_codes(self):
        for code in (0, 99, 2):
            with self.subTest(code=code):
                self.run_fixture(False, code)

    def test_setup_failure_cannot_overwrite_another_unknown_summary(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(load, "RESULTS", Path(folder)):
            root = Path(folder)
            (root / "unknown.json").write_text("preserve", encoding="utf-8")
            isolated = root / ".pending" / "fixture"
            isolated.mkdir(parents=True)
            (isolated / "unknown.json").write_text('{"run_id":"unknown"}', encoding="utf-8")
            self.assertTrue(load.promote_summary("fixture", "scale").startswith("missing"))
            self.assertEqual((root / "unknown.json").read_text(), "preserve")
            self.assertTrue((isolated / "unknown.json").exists())

    def test_main_adds_remote_metadata_without_claiming_delivery(self):
        for code in (0, 99):
            with self.subTest(code=code):
                self.run_fixture(True, code)


class ComparisonTests(unittest.TestCase):
    def fixture_pair(self, directory):
        metadata = obs.comparison_context("scale", [], {"cpus": 4}, "k6 fixture", {"product-service": ["fixture-image"]})
        for run_id, replicas, start in (("fixture-one", 1, 1000), ("fixture-three", 3, 2000)):
            summary = {"run_id": run_id, "scenario": "scale", "scale_config": {"sustained_s": 60},
                       "metrics": {"checks": {"thresholds": {"rate>0.99": {"ok": True}}}}}
            context = {"run_id": run_id, "scenario": "scale", "product_service_replicas": replicas,
                       "k6_exit_code": 0, "started_at_epoch_ms": start, "finished_at_epoch_ms": start + 500,
                       "observability": {"comparison_context": metadata, "comparison_key": obs.comparison_key(metadata),
                                         "output": "experimental-prometheus-rw", "export_status": "samples_observed",
                                         "labels": obs.export_arguments(run_id, "scale", replicas, key=obs.comparison_key(metadata))[1]}}
            for suffix, value in ((".json", summary), ("-env.json", context)):
                (directory / (run_id + suffix)).write_text(json.dumps(value), encoding="utf-8")

    def test_compatible_completed_runs_produce_a_real_time_range_link(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture_pair(Path(folder))
            url = obs.verify_comparison("fixture-one", "fixture-three", Path(folder))
            self.assertIn("var-comparison_checked=1", url)
            self.assertIn("from=1000", url)
            self.assertIn("to=2500", url)

    def test_failed_unexported_or_incompatible_runs_are_rejected(self):
        changes = (lambda c: c.update(k6_exit_code=99),
                   lambda c: c["observability"].update(export_status="unverified"),
                   lambda c: c["observability"].update(comparison_key="different"),
                   lambda c: c.update(product_service_replicas=1))
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                self.fixture_pair(directory)
                path = directory / "fixture-three-env.json"
                context = json.loads(path.read_text())
                change(context)
                path.write_text(json.dumps(context), encoding="utf-8")
                with self.assertRaises(ValueError):
                    obs.verify_comparison("fixture-one", "fixture-three", directory)

    def test_comparison_key_changes_with_load_environment_and_images(self):
        base = obs.comparison_context("scale", [], {"cpus": 4}, "fixture", {"product-service": ["one"]})
        for key, value in (("extra_env", ["MAX_VUS=20"]), ("environment", {"cpus": 8}),
                           ("application_images", {"product-service": ["two"]})):
            self.assertNotEqual(obs.comparison_key(base), obs.comparison_key({**base, key: value}))


if __name__ == "__main__":
    unittest.main()
