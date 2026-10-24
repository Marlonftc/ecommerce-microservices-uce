"""Static contracts and HTTP failure fixtures. No containers are stopped or changed."""
import json
import unittest
from unittest.mock import patch

import check_observability as checks
from generate_dashboard import APIS, COMPONENTS, HERE, dashboard, generated_files, rule_tests, targets


class ObservabilityTests(unittest.TestCase):
    def test_generated_files_are_current(self):
        for name, content in generated_files().items():
            with self.subTest(name=name):
                self.assertEqual((HERE / name).read_text(encoding="utf-8"), content)

    def test_only_real_http_health_targets(self):
        rows = targets()
        self.assertEqual(len(rows), 15)
        for api in APIS:
            self.assertEqual({r["targets"][0] for r in rows if r["labels"]["component"] == api},
                             {f"http://{api}:8000/health/live", f"http://{api}:8000/health/ready"})
        text = json.dumps(rows)
        self.assertNotIn("postgres", text)
        self.assertNotIn("/internal", text)

    def test_all_eight_components_have_honest_state_cards(self):
        cards = [p for p in dashboard()["panels"] if 10 <= p["id"] <= 17]
        self.assertEqual(len(cards), len(COMPONENTS))
        for card in cards:
            self.assertTrue(card["targets"][0]["instant"])
            self.assertEqual(card["options"]["reduceOptions"]["calcs"], ["last"])
            self.assertEqual(card["fieldConfig"]["defaults"]["noValue"], "UNKNOWN")

    def test_no_fill_zero_or_connection_across_missing_latency(self):
        for graph in (p for p in dashboard()["panels"] if p["type"] == "timeseries"):
            self.assertFalse(graph["fieldConfig"]["defaults"]["custom"]["spanNulls"])
            self.assertIn("uce:probe_success:fresh", graph["targets"][0]["expr"])
            self.assertNotIn("vector(0)", graph["targets"][0]["expr"])

    def test_timeline_keeps_discrete_states_instead_of_numeric_buckets(self):
        timeline = next(p for p in dashboard()["panels"] if p["type"] == "state-timeline")
        fields = timeline["fieldConfig"]["defaults"]
        self.assertNotEqual(fields["color"]["mode"], "thresholds")
        self.assertNotIn("thresholds", fields)
        self.assertEqual(fields["mappings"][0]["options"]["3"]["text"], "UP")

    def test_rule_fixtures_cover_unknown_and_recovery(self):
        names = [case["name"] for case in rule_tests()["tests"]]
        self.assertTrue(any("old samples" in name for name in names))
        self.assertTrue(any("exporter failure" in name for name in names))
        self.assertTrue(any("unknown gap" in name for name in names))

    def test_compose_scope_and_no_application_env(self):
        compose = (HERE / "compose.observability.yaml").read_text(encoding="utf-8")
        self.assertEqual(compose.count('"127.0.0.1:3000:3000"'), 1)
        self.assertEqual(compose.count("    ports:"), 1)
        for forbidden in ("data-net", "docker.sock", "postgres-data", "k6:", "cadvisor:", "env_file:"):
            self.assertNotIn(forbidden, compose)
        self.assertNotIn("${", compose)
        self.assertNotIn("--web.enable-remote-write-receiver", compose)

    def test_network_preflight_does_not_accept_another_network(self):
        with patch.object(checks, "command", return_value="wrong-network"):
            with self.assertRaises(RuntimeError):
                checks.preflight()

    def test_failed_prometheus_query_is_not_a_metric(self):
        with patch.object(checks, "read_json", return_value={"status": "error", "data": {}}):
            with self.assertRaises(RuntimeError):
                checks.query("uce:component_state")


if __name__ == "__main__":
    unittest.main()
