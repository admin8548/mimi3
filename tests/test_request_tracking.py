import asyncio
import json
import unittest

from fastapi.testclient import TestClient

from mimo2api.gateway_state import state
from mimo2api.web_service import app, cleanup_pending_request, dispatch_to_node


class RequestTrackingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_clients = list(state.active_clients)
        self.old_uids = dict(state.client_uids)
        self.old_cooldowns = dict(state.client_cooldowns)
        self.old_cooldown_reasons = dict(state.client_cooldown_reasons)
        self.old_index = state.current_client_index
        self.old_active_requests = dict(state.active_requests)
        self.old_recent_dispatches = list(state.recent_dispatches)
        self.old_pending_queues = dict(state.pending_queues)
        self.old_ws_to_req_ids = dict(state.ws_to_req_ids)
        self.old_req_id_to_ws_id = dict(state.req_id_to_ws_id)
        self.old_req_id_timestamps = dict(state.req_id_timestamps)

        state.active_clients[:] = []
        state.client_uids.clear()
        state.client_cooldowns.clear()
        state.client_cooldown_reasons.clear()
        state.current_client_index = 0
        state.active_requests.clear()
        state.recent_dispatches.clear()
        state.pending_queues.clear()
        state.ws_to_req_ids.clear()
        state.req_id_to_ws_id.clear()
        state.req_id_timestamps.clear()

    async def asyncTearDown(self):
        state.active_clients[:] = self.old_clients
        state.client_uids.clear()
        state.client_uids.update(self.old_uids)
        state.client_cooldowns.clear()
        state.client_cooldowns.update(self.old_cooldowns)
        state.client_cooldown_reasons.clear()
        state.client_cooldown_reasons.update(self.old_cooldown_reasons)
        state.current_client_index = self.old_index
        state.active_requests.clear()
        state.active_requests.update(self.old_active_requests)
        state.recent_dispatches.clear()
        state.recent_dispatches.extend(self.old_recent_dispatches)
        state.pending_queues.clear()
        state.pending_queues.update(self.old_pending_queues)
        state.ws_to_req_ids.clear()
        state.ws_to_req_ids.update(self.old_ws_to_req_ids)
        state.req_id_to_ws_id.clear()
        state.req_id_to_ws_id.update(self.old_req_id_to_ws_id)
        state.req_id_timestamps.clear()
        state.req_id_timestamps.update(self.old_req_id_timestamps)

    async def test_dispatch_records_active_and_recent_without_sensitive_body(self):
        class ClientInfo:
            host = "10.0.0.5"
            port = 20803

        class DummyWs:
            client = ClientInfo()

            async def send_text(self, payload):
                req_id = json.loads(payload)["req_id"]
                await state.pending_queues[req_id].put({"type": "start", "status": 200, "headers": {}})

        ws = DummyWs()
        state.active_clients.append(ws)
        state.client_uids[id(ws)] = "uid-req"

        prepared = await dispatch_to_node(
            method="POST",
            path="/v1/chat/completions",
            body=json.dumps({"model": "mimo-test", "stream": True, "secret": "DO_NOT_LEAK"}),
            log_label="测试请求",
            attempt_number=2,
            gateway_request_id="gw_test",
            route_key="/v1/responses",
            model="mimo-test",
            stream=True,
        )

        self.assertIsNotNone(prepared)
        snapshot = state.build_requests_snapshot()
        self.assertEqual(len(snapshot["active_requests"]), 1)
        item = snapshot["active_requests"][0]
        self.assertEqual(item["gateway_request_id"], "gw_test")
        self.assertEqual(item["node_request_id"], prepared.req_id)
        self.assertEqual(item["route"], "/v1/responses")
        self.assertEqual(item["upstream_path"], "/v1/chat/completions")
        self.assertEqual(item["model"], "mimo-test")
        self.assertTrue(item["stream"])
        self.assertEqual(item["node_uid"], "uid-req")
        self.assertEqual(item["node_addr"], "10.0.0.5:20803")
        self.assertEqual(item["attempt"], 2)
        self.assertNotIn("DO_NOT_LEAK", json.dumps(snapshot))

        cleanup_pending_request(prepared.req_id, end_reason="completed", status_code=200)
        snapshot = state.build_requests_snapshot()
        self.assertEqual(snapshot["active_requests"], [])
        self.assertEqual(len(snapshot["recent_dispatches"]), 1)
        self.assertEqual(snapshot["recent_dispatches"][0]["end_reason"], "completed")
        self.assertEqual(snapshot["recent_dispatches"][0]["status_code"], 200)


class RequestTrackingRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.old_active_requests = dict(state.active_requests)
        self.old_recent_dispatches = list(state.recent_dispatches)
        state.active_requests.clear()
        state.recent_dispatches.clear()

    def tearDown(self):
        state.active_requests.clear()
        state.active_requests.update(self.old_active_requests)
        state.recent_dispatches.clear()
        state.recent_dispatches.extend(self.old_recent_dispatches)

    def test_api_requests_active_empty_shape(self):
        resp = self.client.get("/api/requests/active")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("generated_at", payload)
        self.assertEqual(payload["active_requests"], [])
        self.assertEqual(payload["recent_dispatches"], [])

    def test_webui_contains_request_node_panel(self):
        html = self.client.get("/webui").text
        self.assertIn("当前请求节点", html)
        self.assertIn("/api/requests/active", html)
