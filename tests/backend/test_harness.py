"""Harness-only tests. Docker, real SQL and backend suites are never used."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

import isolation_guard as guard
import run_isolated_backend as harness


RUN = "backend-20261025-120000"
CID, NID = "a" * 64, "b" * 64


class TemporaryRun(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / "work" / RUN
        self.results = self.root / "results" / RUN
        self.work.mkdir(parents=True)
        self.results.mkdir(parents=True)
        self.patches = [patch.object(harness, "WORK_BASE", self.root / "work"),
                        patch.object(harness, "RESULT_BASE", self.root / "results")]
        for replacement in self.patches:
            replacement.start()
        self.manifest = {"purpose": harness.PURPOSE, "run_id": RUN, "nonce": "c" * 32,
                         "workspace": str(self.work.resolve()), "results": str(self.results.resolve()),
                         "container_name": "uce-backend-pg-" + RUN, "network_name": "uce-backend-net-" + RUN,
                         "container_id": CID, "network_id": NID, "cluster": "uce-test-" + "c" * 32,
                         "image": harness.IMAGE, "port": harness.PORT, "before": {}, "services": {}}
        harness.save_manifest(self.manifest)
        self.policy = harness.policy_for(self.manifest, "order-service", "disposable-password")
        self.params = {"host": "127.0.0.1", "port": "55433", "dbname": "orders_db", "user": "order_svc",
                       "password": "disposable-password"}

    def tearDown(self):
        for replacement in reversed(self.patches):
            replacement.stop()
        self.temp.cleanup()

    def container(self):
        return {"id": CID, "name": "/" + self.manifest["container_name"], "image": harness.IMAGE,
                "labels": harness.labels(self.manifest), "memory": 512 * 1024 ** 2,
                "swap": 512 * 1024 ** 2, "cpus": 1_000_000_000, "state": "running", "health": "healthy",
                "ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55433"}]},
                "networks": {self.manifest["network_name"]: {"NetworkID": NID}},
                "mounts": [{"Type": "tmpfs", "Destination": "/var/lib/postgresql/data"},
                           {"Type": "bind", "Destination": "/docker-entrypoint-initdb.d", "RW": False,
                            "Source": str((self.work / "init").resolve())}],
                "host_mounts": [{"Type": "tmpfs", "Target": "/var/lib/postgresql/data",
                                 "TmpfsOptions": {"SizeBytes": 256 * 1024 ** 2}}]}

    def network(self, occupied=False):
        return {"id": NID, "name": self.manifest["network_name"], "driver": "bridge",
                "labels": harness.labels(self.manifest), "containers": {CID: {}} if occupied else {}}


class DestinationTests(TemporaryRun):
    def test_exact_target_and_order_schema_are_allowed(self):
        guard.validate_target(self.policy, self.params)
        guard.validate_target(self.policy, dict(self.params, options="-c search_path=order_tests"))

    def test_active_or_ambiguous_destinations_are_rejected_before_connect(self):
        changes = ({"host": "postgres"}, {"host": "localhost"}, {"port": "5433"}, {"port": "5432"},
                   {"host": "127.0.0.1,127.0.0.2"}, {"dbname": "postgres"}, {"user": "postgres"},
                   {"password": "other-password"}, {"hostaddr": "127.0.0.2"}, {"service": "live"},
                   {"passfile": "some-file"}, {"options": "-c search_path=public"})
        for change in changes:
            with self.subTest(change=next(iter(change))):
                original = Mock()
                parser = Mock(return_value=dict(self.params, **change))
                with self.assertRaises(guard.IsolationViolation):
                    guard.checked_connect(self.policy, original, parser)
                original.assert_not_called()

    def test_missing_password_or_port_is_rejected(self):
        for name in ("password", "port", "host", "dbname", "user"):
            data = dict(self.params)
            del data[name]
            with self.assertRaises(guard.IsolationViolation):
                guard.validate_target(self.policy, data)

    def test_exact_identity_is_read_before_returning_connection(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (self.policy["cluster"], "orders_db", "order_svc")
        original = Mock(return_value=connection)
        result = guard.checked_connect(self.policy, original, Mock(return_value=self.params))
        self.assertIs(result, connection)
        cursor.execute.assert_called_once_with("SELECT current_setting('cluster_name'), current_database(), current_user")
        connection.rollback.assert_called_once()
        connection.close.assert_not_called()

    def test_wrong_server_database_or_role_closes_connection(self):
        for identity in (("live", "orders_db", "order_svc"),
                         (self.policy["cluster"], "other", "order_svc"),
                         (self.policy["cluster"], "orders_db", "postgres"), None):
            connection = MagicMock()
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = identity
            with self.assertRaises(guard.IsolationViolation):
                guard.checked_connect(self.policy, Mock(return_value=connection), Mock(return_value=self.params))
            connection.close.assert_called_once()

    def test_connection_failure_has_no_raw_dsn_or_password(self):
        original = Mock(side_effect=RuntimeError("disposable-password sensitive-dsn"))
        with self.assertRaises(guard.DatabaseUnavailable) as caught:
            guard.checked_connect(self.policy, original, Mock(return_value=self.params))
        self.assertNotIn("disposable-password", str(caught.exception))
        self.assertFalse(issubclass(guard.IsolationViolation, Exception))

    def test_bad_conninfo_cannot_reach_driver(self):
        original = Mock()
        with self.assertRaises(guard.IsolationViolation):
            guard.checked_connect(self.policy, original, Mock(side_effect=ValueError("bad")))
        original.assert_not_called()


class SpawnTests(TemporaryRun):
    def setUp(self):
        super().setUp()
        self.env = {"PYTHONPATH": self.policy["service_dir"]}
        self.options = {"env": self.env, "cwd": str(self.work)}

    def test_existing_worker_and_config_subprocesses_are_supported(self):
        guard.validate_spawn(self.policy, [sys.executable, "-m", "app.worker"], self.options)
        guard.validate_spawn(self.policy, [sys.executable, "-c", "import app.database"], self.options)

    def test_shell_other_python_and_guard_disable_flags_fail(self):
        options = [([sys.executable, "-S", "x"], self.options),
                   ([sys.executable, "-BS", "x"], self.options),
                   ([sys.executable, "-I", "x"], self.options),
                   ([sys.executable, "-E", "x"], self.options),
                   ([sys.executable, "-m", "app.worker"], dict(self.options, shell=True)),
                   (["some-other-python", "-m", "app.worker"], self.options),
                   ("python -m app.worker", self.options)]
        for args, kwargs in options:
            with self.assertRaises(guard.IsolationViolation):
                guard.validate_spawn(self.policy, args, kwargs)

    def test_missing_guard_path_or_outside_working_directory_fails(self):
        for options in (dict(self.options, env={}), dict(self.options, cwd=str(self.root))):
            with self.assertRaises(guard.IsolationViolation):
                guard.validate_spawn(self.policy, [sys.executable, "-m", "app.worker"], options)


class ResourceTests(TemporaryRun):
    def test_container_plan_has_tmpfs_local_port_and_no_persistent_volume(self):
        command = harness.container_args(self.manifest)
        self.assertIn("127.0.0.1:55433:5432", command)
        self.assertIn("type=tmpfs,destination=/var/lib/postgresql/data,tmpfs-size=268435456", command)
        self.assertIn(harness.IMAGE, command)
        self.assertEqual(command[command.index("--pull") + 1], "never")
        for forbidden in ("--privileged", "--env-file", "--volumes-from", "--volume", "-v"):
            self.assertNotIn(forbidden, command)
        self.assertNotIn("POSTGRES_PASSWORD=", " ".join(command))
        harness.verify_container(self.container(), self.manifest)

    def test_active_network_volume_port_image_or_limits_are_rejected(self):
        base = self.container()
        mutations = [{"networks": {"ecommerce-uce_data-net": {"NetworkID": NID}}},
                     {"ports": {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "55433"}]}},
                     {"mounts": [{"Type": "volume", "Name": "postgres-data"}]},
                     {"image": "other"}, {"memory": 0}, {"swap": 0}, {"cpus": 0}, {"host_mounts": []}]
        for change in mutations:
            with self.assertRaises(harness.HarnessError):
                harness.verify_container(dict(base, **change), self.manifest)

    def test_missing_health_does_not_cause_optional_field_exception(self):
        resource = self.container()
        del resource["health"]
        harness.verify_container(resource, self.manifest)
        self.assertIsNone(resource.get("health"))

    def test_network_requires_exact_container_and_nonce(self):
        harness.verify_network(self.network(), self.manifest)
        harness.verify_network(self.network(True), self.manifest, with_container=True)
        for resource in (dict(self.network(), containers={"d" * 64: {}}),
                         dict(self.network(), labels={}), dict(self.network(), id="d" * 64)):
            with self.assertRaises(harness.HarnessError):
                harness.verify_network(resource, self.manifest)

    def test_bind_source_maps_windows_desktop_without_allowing_prefixes(self):
        self.assertTrue(harness.same_bind_source('/run/desktop/mnt/host/c/Test/init', 'C:\\Test\\init'))
        self.assertFalse(harness.same_bind_source('/run/desktop/mnt/host/c/Test/init-other', 'C:\\Test\\init'))

    def test_invalid_ids_and_traversal_fail(self):
        for value in ("ecommerce-uce-postgres-1", "", "a" * 12, "../bad"):
            with self.assertRaises(harness.HarnessError):
                harness.resource_id(value)
        for value in ("../backend-20261025-120000", "ecommerce-uce", RUN + "/bad"):
            with self.assertRaises(harness.HarnessError):
                harness.run_paths(value)

    def test_manifest_names_and_nonce_cannot_be_replaced(self):
        for change in ({"nonce": "bad"}, {"container_name": "ecommerce-uce-postgres-1"},
                       {"workspace": str(self.root)}, {"port": 5433}, {"cluster": "live"}):
            harness.write_json(self.work / "manifest.json", dict(self.manifest, **change))
            with self.assertRaises(harness.HarnessError):
                harness.read_manifest(RUN)


class EvidenceTests(TemporaryRun):
    def test_junit_requires_exact_count_zero_failures_and_zero_skips(self):
        report = self.results / "sample.xml"
        for content, expected, accepted in (("<testcase name='a'/>", 1, True),
                                             ("<testcase><skipped/></testcase>", 1, False),
                                             ("<testcase><failure/></testcase>", 1, False),
                                             ("<testcase><error/></testcase>", 1, False),
                                             ("<testcase/>", 2, False)):
            report.write_text("<testsuites><testsuite>" + content + "</testsuite></testsuites>")
            self.assertEqual(harness.junit_result(report, expected)["accepted"], accepted)

    def test_missing_junit_is_not_success(self):
        with self.assertRaises(harness.HarnessError):
            harness.junit_result(self.results / "missing.xml", 1)

    def test_ambient_secrets_and_libpq_settings_are_not_inherited(self):
        with patch.dict(os.environ, {"DB_PASSWORD": "active", "ADMIN_PASSWORD": "active",
                                    "PGHOST": "active", "JWT_PRIVATE_KEY_PATH": "active"}):
            env = harness.child_environment(self.manifest, "order-service", "disposable")
        self.assertEqual(env["DB_PORT"], "55433")
        self.assertEqual(env["DB_PASSWORD"], "disposable")
        self.assertFalse(any(k.startswith("PG") for k in env))
        self.assertNotIn("ADMIN_PASSWORD", env)
        self.assertNotIn("JWT_PRIVATE_KEY_PATH", env)
        self.assertEqual(env["DB_SCHEMA"], "order_tests")

    def test_redaction_and_audit_never_need_credentials_in_reports(self):
        self.assertEqual(harness.redact("token=0123456789abcdef", {"x": "0123456789abcdef"}),
                         "token=[REDACTED_TEST_SECRET]")
        self.assertNotIn("password", self.policy)

    def test_subprocess_boot_is_required_even_when_pid_is_a_launcher(self):
        rows = [{"kind": "startup", "pid": 10, "nonce": self.manifest["nonce"], "token": "ack"},
                {"kind": "spawn", "pid": 1, "child_pid": 9, "nonce": self.manifest["nonce"], "token": "ack"}]
        path = self.work / "guard-events.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows))
        self.assertEqual(len(harness.guard_events(self.manifest)), 2)
        for changed in (rows[1:], rows + [{"kind": "violation", "nonce": self.manifest["nonce"]}],
                        [dict(rows[0], nonce="wrong")]):
            path.write_text("\n".join(json.dumps(row) for row in changed))
            with self.assertRaises(harness.HarnessError):
                harness.guard_events(self.manifest)

    def test_empty_guard_audit_is_not_success(self):
        (self.work / "guard-events.jsonl").write_text('')
        with self.assertRaises(harness.HarnessError):
            harness.guard_events(self.manifest)

    def test_parallel_audit_records_do_not_overwrite_each_other(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda n: guard.event(self.policy, 'fixture', sequence=n), range(80)))
        rows = [json.loads(line) for line in (self.work / "guard-events.jsonl").read_text().splitlines()]
        self.assertEqual({row['sequence'] for row in rows}, set(range(80)))
        self.assertEqual(len(rows), 80)

    def test_cli_has_no_guard_or_skip_bypass(self):
        for option in ("--skip-preflight", "--no-guard", "--allow-production", "--allow-skips"):
            with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                harness.parser().parse_args(["run", "--run-id", RUN, "--services", "order-service", option])

    def test_fixed_resource_limits_are_checked_before_docker(self):
        args = argparse.Namespace(run_id=RUN, port=5433, image=harness.IMAGE,
                                  memory="512m", tmpfs_size="256m", cpus=1)
        with patch.object(harness.Docker, "call", side_effect=AssertionError("Docker must not run")):
            with self.assertRaises(harness.HarnessError):
                harness.prepare(args)


class CleanupTests(TemporaryRun):
    def test_cleanup_uses_exact_ids_and_leaves_reports(self):
        docker = Mock()
        docker.call.side_effect = [json.dumps(self.container()), "", json.dumps(self.network()), "", ""]
        with patch.object(harness, "Docker", return_value=docker), patch.object(harness, "stop_test_processes"), patch('sys.stdout', io.StringIO()):
            harness.cleanup(argparse.Namespace(run_id=RUN))
        calls = [call.args for call in docker.call.call_args_list]
        self.assertIn(("rm", "--force", CID), calls)
        self.assertIn(("network", "rm", NID), calls)
        self.assertFalse(self.work.exists())
        self.assertTrue((self.results / "cleanup.json").is_file())
        self.assertFalse(any("prune" in command or "down" in command or "volume" in command for command in calls))

    def test_wrong_container_owner_prevents_any_removal(self):
        docker = Mock()
        docker.call.return_value = json.dumps(dict(self.container(), labels={}))
        with patch.object(harness, "Docker", return_value=docker), self.assertRaises(harness.HarnessError):
            harness.cleanup(argparse.Namespace(run_id=RUN))
        self.assertFalse(any(call.args[0] == "rm" for call in docker.call.call_args_list))
        self.assertTrue(self.work.exists())

    def test_occupied_network_is_never_removed(self):
        manifest = dict(self.manifest)
        del manifest["container_id"]
        harness.save_manifest(manifest)
        docker = Mock()
        docker.call.return_value = json.dumps(self.network(True))
        with patch.object(harness, "Docker", return_value=docker), self.assertRaises(harness.HarnessError):
            harness.cleanup(argparse.Namespace(run_id=RUN))
        self.assertFalse(any(call.args[:2] == ("network", "rm") for call in docker.call.call_args_list))

    def test_lost_create_response_recovers_only_one_exact_name(self):
        del self.manifest["container_id"]
        self.manifest["container_requested"] = True
        candidate = self.container()
        candidate["id"] = CID
        docker = Mock()
        docker.call.return_value = json.dumps(candidate)
        harness.recover_creation(docker, self.manifest, "container")
        self.assertEqual(self.manifest["container_id"], CID)
        self.assertEqual(docker.call.call_args.args[-1], self.manifest["container_name"])

    def test_lost_response_never_adopts_a_different_nonce(self):
        del self.manifest["container_id"]
        self.manifest["container_requested"] = True
        docker = Mock()
        docker.call.return_value = json.dumps(dict(self.container(), labels={}))
        with self.assertRaises(harness.HarnessError):
            harness.recover_creation(docker, self.manifest, "container")
        self.assertNotIn("container_id", self.manifest)

    def test_process_cleanup_requires_nonce_pid_and_creation_identity(self):
        path = self.work / "guard-events.jsonl"
        row = {"kind": "startup", "pid": 123, "birth": "456", "nonce": self.manifest["nonce"]}
        path.write_text(json.dumps(row))
        with patch.object(guard, "stop_process") as stop:
            harness.stop_test_processes(self.manifest)
            stop.assert_called_once_with(123, "456")
        path.write_text(json.dumps(dict(row, nonce="other")))
        with patch.object(guard, "stop_process") as stop, self.assertRaises(harness.HarnessError):
            harness.stop_test_processes(self.manifest)
        stop.assert_not_called()

    def test_pid_reuse_cannot_terminate_another_process(self):
        with patch.object(guard, "process_birth", return_value="different"):
            self.assertFalse(guard.stop_process(123, "456"))

    def test_workspace_cannot_be_deleted_after_ownership_change(self):
        original = dict(self.manifest)
        self.manifest["nonce"] = "d" * 32
        self.manifest["cluster"] = "uce-test-" + self.manifest["nonce"]
        harness.save_manifest(self.manifest)
        with self.assertRaises(harness.HarnessError):
            harness.safe_delete_workspace(original)
        self.assertTrue(self.work.exists())


class StartupTests(TemporaryRun):
    """Actual Python startup, without executing any SQL or backend test code."""

    def boot(self, code, broken=False):
        source = Path(self.policy["service_dir"])
        source.mkdir(parents=True)
        (self.work / "tmp").mkdir()
        (source / "isolation_guard.py").write_bytes((harness.HERE / "isolation_guard.py").read_bytes())
        harness.write_json(source / "guard-policy.json", self.policy)
        no_sql = ("import psycopg\nfrom pathlib import Path\n"
                  "def _sql_must_never_run(*args, **kwargs):\n"
                  "    Path('sql-driver-touched').write_text('unexpected')\n"
                  "    raise RuntimeError('SQL disabled in harness unit tests')\n"
                  "psycopg.connect = _sql_must_never_run\n")
        (source / "sitecustomize.py").write_text(no_sql + harness.startup_source(), encoding="utf-8")
        if broken:
            (source / "isolation_guard.py").write_text("raise RuntimeError('broken test fixture')")
        env = harness.child_environment(self.manifest, "order-service", "disposable-password")
        return subprocess.run([sys.executable, "-B", "-c", code], cwd=source, env=env,
                              capture_output=True, text=True, timeout=20)

    def test_guard_loads_and_reaches_a_subprocess_without_sql(self):
        code = ("import sys,subprocess,builtins; "
                "print('PARENT',builtins._uce_guard_nonce); "
                "subprocess.run([sys.executable,'-B','-c',"
                "\"import builtins; print('CHILD',builtins._uce_guard_nonce)\"],check=True)")
        result = self.boot(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PARENT " + self.manifest["nonce"], result.stdout)
        self.assertIn("CHILD " + self.manifest["nonce"], result.stdout)
        events = harness.guard_events(self.manifest)
        self.assertEqual(sum(row["kind"] == "startup" for row in events), 2)
        self.assertEqual(sum(row["kind"] == "spawn" for row in events), 1)
        self.assertFalse(any(row["kind"] == "sql_verified" for row in events))

    def test_driver_call_to_active_port_is_blocked_before_mock_driver(self):
        code = ("import psycopg,isolation_guard as g\ntry:\n"
                "    psycopg.connect(host='127.0.0.1',port=5433,dbname='orders_db',user='order_svc',"
                "password='disposable-password')\nexcept g.IsolationViolation:\n"
                "    print('BLOCKED_BEFORE_CONNECT')\nelse:\n    raise SystemExit(99)\n")
        result = self.boot(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BLOCKED_BEFORE_CONNECT", result.stdout)
        self.assertFalse((Path(self.policy["service_dir"]) / "sql-driver-touched").exists())
        rows = [json.loads(line) for line in (self.work / "guard-events.jsonl").read_text().splitlines()]
        self.assertFalse(any(row["kind"] == "sql_verified" for row in rows))
        self.assertTrue(any(row["kind"] == "violation" for row in rows))

    def test_missing_guard_is_fatal_before_user_code(self):
        result = self.boot("print('MUST_NOT_RUN')", broken=True)
        self.assertEqual(result.returncode, 86)
        self.assertNotIn("MUST_NOT_RUN", result.stdout)
        self.assertIn("ISOLATION STARTUP FAILED", result.stderr)

    def test_environment_file_open_is_blocked_in_guarded_python(self):
        result = self.boot("from pathlib import Path; Path('.env').read_text()")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Environment file access is forbidden", result.stderr)


class WorkflowTests(TemporaryRun):
    def test_missing_resource_is_not_confused_with_a_docker_failure(self):
        docker = harness.Docker()
        for message, absent in (("Error: No such object: " + CID, True),
                                 ("Error: network " + CID + " not found", True),
                                 ("Docker context not found", False), ("permission denied", False)):
            response = subprocess.CompletedProcess([], 1, "", message)
            with patch.object(subprocess, "run", return_value=response):
                if absent:
                    self.assertIsNone(docker.call("inspect", CID, absent_ok=True))
                else:
                    with self.assertRaises(harness.HarnessError):
                        docker.call("inspect", CID, absent_ok=True)

    def test_two_actions_cannot_run_concurrently(self):
        with patch.object(guard, "process_birth", return_value="123"):
            with harness.action_lock(RUN):
                with self.assertRaises(harness.HarnessError):
                    with harness.action_lock(RUN):
                        self.fail("Concurrent action entered")
        self.assertFalse((self.work / "action.lock").exists())

    def test_stale_owned_action_lock_can_be_recovered(self):
        harness.write_json(self.work / "action.lock", {"pid": 123, "birth": "old", "nonce": self.manifest["nonce"]})
        with patch.object(guard, "process_birth", return_value="new"):
            with harness.action_lock(RUN):
                self.assertTrue((self.work / "action.lock").is_file())
        self.assertFalse((self.work / "action.lock").exists())

    def test_changed_lock_nonce_is_never_removed(self):
        harness.write_json(self.work / "action.lock", {"pid": 123, "birth": "old", "nonce": "other"})
        with self.assertRaises(harness.HarnessError):
            with harness.action_lock(RUN):
                self.fail("Invalid lock entered")
        self.assertTrue((self.work / "action.lock").exists())

    def test_run_executes_migration_then_unchanged_suite_and_preserves_junit(self):
        self.manifest["ready"] = True
        harness.save_manifest(self.manifest)
        (self.work / "test-credentials.json").write_text(json.dumps({"payment-service": "temporary"}))
        calls = []

        def child(manifest, credentials, service, mode):
            calls.append(mode)
            if mode == "test":
                (self.results / (service + ".xml")).write_text(
                    '<testsuites><testsuite>' + '<testcase/>' * 41 + '</testsuite></testsuites>')
            return 0

        with patch.object(harness, "Docker"), patch.object(harness, "inspect_owned") as inspect, \
                patch.object(harness, "verify_container"), patch.object(harness, "verify_network"), \
                patch.object(harness, "verify_sources"), patch.object(harness, "guard_events", return_value=[
                    {"kind": "sql_verified", "service": "payment-service"}]), \
                patch.object(harness, "run_child", side_effect=child), patch('sys.stdout', io.StringIO()):
            harness.run_services(argparse.Namespace(run_id=RUN, services=["payment-service"]))
        self.assertEqual(calls, ["migrate", "test"])
        result = json.loads((self.results / "summary.json").read_text())["payment-service"]
        self.assertTrue(result["accepted"])
        self.assertEqual(result["passed"], 41)
        self.assertEqual(inspect.call_count, 2)

    def test_order_migrations_remain_the_existing_fixture_responsibility(self):
        self.manifest["ready"] = True
        harness.save_manifest(self.manifest)
        (self.work / "test-credentials.json").write_text(json.dumps({"order-service": "temporary"}))
        with patch.object(harness, "Docker"), patch.object(harness, "inspect_owned"), \
                patch.object(harness, "verify_container"), patch.object(harness, "verify_network"), \
                patch.object(harness, "verify_sources"), \
                patch.object(harness, "run_child", return_value=1) as child:
            with self.assertRaises(harness.HarnessError):
                harness.run_services(argparse.Namespace(run_id=RUN, services=["order-service"]))
        self.assertEqual(child.call_count, 1)
        self.assertEqual(child.call_args.args[-1], "test")

    def test_snapshot_distinguishes_health_from_running(self):
        docker = Mock()
        docker.call.return_value = json.dumps({"ID": "abc", "Names": "service", "Image": "image",
                                               "State": "running", "Status": "Up 1 minute (unhealthy)"})
        self.assertEqual(harness.snapshot(docker)["abc"]["Health"], "unhealthy")

    def test_guard_support_hashes_detect_policy_changes(self):
        for service in harness.SPECS:
            folder = self.work / "sources" / service
            folder.mkdir(parents=True)
            for name in ("sitecustomize.py", "isolation_guard.py", "_uce_backend_child.py", "guard-policy.json"):
                (folder / name).write_text('unchanged')
        (self.work / "init").mkdir()
        (self.work / "init/01-init-databases.sh").write_text('unchanged')
        harness.write_json(self.work / "source-hashes.json", {})
        harness.seal_support(self.manifest)
        harness.verify_sources(self.manifest)
        (self.work / "sources/order-service/guard-policy.json").write_text('changed')
        with self.assertRaises(harness.HarnessError):
            harness.verify_sources(self.manifest)


if __name__ == "__main__":
    unittest.main()
