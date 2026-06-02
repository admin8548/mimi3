import unittest

from fastapi.testclient import TestClient

from mimo2api.web_service import app, build_route_diagnostics


class GatewayDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_route_diagnostics_reports_compact_routes(self):
        diagnostics = build_route_diagnostics()
        self.assertTrue(diagnostics["key_routes"]["/v1/responses"]["present"])
        self.assertTrue(diagnostics["key_routes"]["/v1/responses/compact"]["present"])
        self.assertTrue(diagnostics["key_routes"]["/v1/responses/compact/"]["present"])

    def test_compact_invalid_json_returns_openai_style_error(self):
        resp = self.client.post("/v1/responses/compact", content="{bad", headers={"content-type": "application/json"})
        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertIn("error", payload)
        self.assertEqual(payload["error"]["type"], "invalid_request_error")
        self.assertEqual(payload["error"]["code"], 400)

    def test_compact_trailing_slash_works(self):
        resp = self.client.post("/v1/responses/compact/", content="{bad", headers={"content-type": "application/json"})
        self.assertEqual(resp.status_code, 400)


class RouteDiagnosticsConfigTests(unittest.TestCase):
    def test_route_diagnostics_includes_config_summary(self):
        diagnostics = build_route_diagnostics()
        self.assertIn("config", diagnostics)
        self.assertIn("model_mapping_path", diagnostics["config"])
        self.assertIn("metrics_db_path", diagnostics["config"])
        self.assertIn("metrics_snapshot_path", diagnostics["config"])
        self.assertIn("metrics_bucket_seconds", diagnostics["config"])
        self.assertIn("allow_legacy_ws_fallback", diagnostics["config"])
        self.assertIn("max_pending_queues", diagnostics["config"])
