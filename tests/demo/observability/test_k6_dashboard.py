"""Dashboard and smoke contracts; fixtures never enter the running Prometheus."""
import json
import unittest
from pathlib import Path

from generate_k6_dashboard import HERE, dashboard


class K6DashboardTests(unittest.TestCase):
    def test_provisioned_dashboard_matches_source_and_is_separate(self):
        data = json.loads((HERE / "grafana/dashboards/uce-k6.json").read_text(encoding="utf-8"))
        self.assertEqual(data, dashboard())
        self.assertEqual(data["uid"], "uce-k6")
        self.assertEqual(data["title"], "UCE — Pruebas de carga k6")
        self.assertEqual({p["datasource"]["uid"] for p in data["panels"] if "datasource" in p}, {"uce-prometheus"})

    def test_missing_data_does_not_become_availability_or_zero(self):
        for panel in dashboard()["panels"]:
            if panel["type"] == "timeseries":
                self.assertFalse(panel["fieldConfig"]["defaults"]["custom"]["spanNulls"])
            if "fieldConfig" in panel:
                self.assertEqual(panel["fieldConfig"]["defaults"]["noValue"], "Sin datos")
            for target in panel.get("targets", []):
                self.assertNotIn("or vector(0)", target["expr"])
                self.assertNotIn("avg(", target["expr"])
            if panel["type"] == "stat":
                self.assertEqual(panel["fieldConfig"]["defaults"]["color"], {"mode": "fixed", "fixedColor": "#53657a"})

    def test_latency_uses_histograms_and_explicit_seconds_to_ms_conversion(self):
        panels = [p for p in dashboard()["panels"] if "p95" in p.get("title", "")]
        for panel in panels:
            self.assertEqual(panel["fieldConfig"]["defaults"]["unit"], "ms")
            for target in panel["targets"]:
                self.assertIn("histogram_quantile(0.95", target["expr"])
                self.assertIn("k6_http_req_duration_seconds", target["expr"])
                self.assertIn("1000 *", target["expr"])
                self.assertIn("[30s]", target["expr"])

    def test_errors_use_weighted_counter_rates_not_rate_of_rate_gauges(self):
        for panel in (p for p in dashboard()["panels"] if "Errores HTTP" in p.get("title", "")):
            for target in panel["targets"]:
                self.assertIn('expected_response="false"', target["expr"])
                self.assertIn("k6_http_reqs_total", target["expr"])
                self.assertNotIn("k6_http_req_failed_rate", target["expr"])
                self.assertIn("> 0", target["expr"])

    def test_comparison_requires_verified_link_and_matching_config_without_time_shift(self):
        for panel in (p for p in dashboard()["panels"] if p["id"] in (40, 41, 42)):
            for target in panel["targets"]:
                self.assertIn('measurement_kind="benchmark"', target["expr"])
                self.assertIn("comparison_key", target["expr"])
                self.assertIn("${comparison_checked:raw}) == 1", target["expr"])
                self.assertIn("@ end()", target["expr"])
                self.assertNotIn("offset", target["expr"])
        validation = next(v for v in dashboard()["templating"]["list"] if v["name"] == "comparison_checked")
        self.assertEqual(validation["current"]["value"], "0")

    def test_optional_overlay_does_not_publish_ports_or_change_load_mounts(self):
        overlay = (HERE.parents[1] / "load/compose.prometheus.yaml").read_text(encoding="utf-8")
        self.assertIn("external: true", overlay)
        self.assertIn("uce-observability_monitoring", overlay)
        for forbidden in ("ports:", "volumes:", "data-net", "environment:"):
            self.assertNotIn(forbidden, overlay)

    def test_smoke_is_bounded_anonymous_and_has_no_business_writes(self):
        smoke = (HERE.parents[1] / "load/smoke_prometheus.js").read_text(encoding="utf-8")
        self.assertIn("vus: 1, iterations: 8", smoke)
        self.assertIn("redirects: 0", smoke)
        self.assertIn("sleep(5)", smoke)
        self.assertIn("/api/products?limit=1", smoke)
        for forbidden in ("http.post", "http.put", "http.del", "http.patch", "adminToken", "createProducts"):
            self.assertNotIn(forbidden, smoke)
        compose = (HERE / "compose.k6-smoke.yaml").read_text(encoding="utf-8")
        for forbidden in ("ADMIN_", "data-net", "ports:", "docker.sock", ".env"):
            self.assertNotIn(forbidden, compose)
        self.assertIn("load/results/smoke:/results", compose)


if __name__ == "__main__":
    unittest.main()
