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


class GatewayUpstream400HandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_end_of_data_400_does_not_retire_node(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from mimo2api import web_service

        body = '{"error":{"message":"unexpected end of data"}}'
        queue = asyncio.Queue()
        queue.put_nowait({"type": "chunk", "body": body})
        queue.put_nowait({"type": "finish"})
        fake_ws = SimpleNamespace(client=SimpleNamespace(host="fake-node"))
        forward_attempt = web_service.ForwardAttempt(
            req_id="req_test",
            queue=queue,
            target_ws=fake_ws,
            first_msg={"type": "headers", "status": 400},
            attempt_number=1,
        )

        async def fake_dispatch_to_node(**kwargs):
            return forward_attempt

        with patch.object(web_service, "dispatch_to_node", fake_dispatch_to_node), \
             patch.object(web_service, "retire_client", AsyncMock()) as retire_mock:
            prepared = await web_service.prepare_forward_attempt(
                method="POST",
                path="/v1/chat/completions",
                body='{"model":"mimo-v2.5","messages":[]}',
                log_label="test",
                retry_state=web_service.RetryState(),
                attempt_number=1,
                gateway_request_id="gw_test",
                route_key="/v1/responses",
                model="mimo-v2.5",
                stream=False,
            )

        self.assertIsNotNone(prepared)
        retire_mock.assert_not_awaited()
        self.assertEqual(prepared.first_msg["status"], 400)
        replayed = await prepared.queue.get()
        self.assertEqual(replayed, {"type": "chunk", "body": body})
