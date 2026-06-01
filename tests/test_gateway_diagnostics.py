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


class DispatchSelectionTests(unittest.TestCase):
    def test_uid_clients_are_preferred_over_legacy(self):
        from mimo2api.gateway_state import state
        from mimo2api.web_service import get_next_client

        class Dummy:
            pass

        legacy = Dummy()
        uid_node = Dummy()
        old_clients = list(state.active_clients)
        old_uids = dict(state.client_uids)
        old_cooldowns = dict(state.client_cooldowns)
        old_index = state.current_client_index
        try:
            state.active_clients[:] = [legacy, uid_node]
            state.client_uids.clear()
            state.client_uids[id(uid_node)] = "uid-1"
            state.client_cooldowns.clear()
            state.current_client_index = 0
            self.assertIs(get_next_client(), uid_node)
        finally:
            state.active_clients[:] = old_clients
            state.client_uids.clear()
            state.client_uids.update(old_uids)
            state.client_cooldowns.clear()
            state.client_cooldowns.update(old_cooldowns)
            state.current_client_index = old_index

    def test_legacy_is_not_selected_when_no_uid_clients_exist(self):
        from mimo2api.gateway_state import state
        from mimo2api.web_service import get_next_client

        class Dummy:
            pass

        legacy = Dummy()
        old_clients = list(state.active_clients)
        old_uids = dict(state.client_uids)
        old_cooldowns = dict(state.client_cooldowns)
        old_index = state.current_client_index
        try:
            state.active_clients[:] = [legacy]
            state.client_uids.clear()
            state.client_cooldowns.clear()
            state.current_client_index = 0
            self.assertIsNone(get_next_client())
        finally:
            state.active_clients[:] = old_clients
            state.client_uids.clear()
            state.client_uids.update(old_uids)
            state.client_cooldowns.clear()
            state.client_cooldowns.update(old_cooldowns)
            state.current_client_index = old_index


class ProtocolTraceTests(unittest.TestCase):
    def test_trace_summary_redacts_sensitive_fields(self):
        from mimo2api.manager import _summarize_value

        summary = _summarize_value({
            "token": "abc123",
            "nested": {"api_key": "secret", "value": 7},
            "items": [1, {"cookie": "xyz"}],
        })
        self.assertEqual(summary["token"], "<redacted>")
        self.assertEqual(summary["nested"]["api_key"], "<redacted>")
        self.assertEqual(summary["nested"]["value"], 7)
        self.assertEqual(summary["items"]["type"], "list")


if __name__ == "__main__":
    unittest.main()


class OpenClawProtocolCatalogTests(unittest.TestCase):
    def test_protocol_catalog_contains_field_and_method_semantics(self):
        from mimo2api.openclaw_protocol import build_protocol_catalog, classify_method, classify_event

        catalog = build_protocol_catalog()
        self.assertEqual(catalog["protocol_version"], 3)
        self.assertIn("id", catalog["envelope_fields"])
        self.assertTrue(catalog["methods"]["agent"]["implemented_in_manager"])
        self.assertTrue(catalog["methods"]["agent"]["mutating_or_execution"])
        self.assertEqual(classify_method("sessions.list")["category"], "sessions")
        self.assertTrue(classify_method("agent.identity.get")["read_only_verified"])
        self.assertTrue(classify_method("exec.approvals.get")["read_only_verified"])
        self.assertEqual(classify_method("exec.approval.request")["parameter_hint"]["required"], ["command"])
        self.assertEqual(classify_method("browser.request")["parameter_hint"]["required"], ["method", "path"])
        self.assertEqual(classify_method("sessions.compact")["parameter_hint"]["required"], ["key"])
        self.assertEqual(classify_event("agent")["hint"]["streams"]["assistant"].startswith("模型文本流"), True)
        self.assertIn("request", classify_event("exec.approval.requested")["hint"]["payload_fields"])

    def test_hello_payload_summary_is_sanitized_and_counted(self):
        from mimo2api.openclaw_protocol import summarize_hello_payload

        summary = summarize_hello_payload({
            "type": "hello-ok",
            "protocol": 3,
            "server": {"version": "2026.3.12", "connId": "secret-conn"},
            "features": {"methods": ["health", "agent"], "events": ["connect.challenge", "agent"]},
            "snapshot": {"health": {"defaultAgentId": "main"}},
            "sessionDefaults": {"mainKey": "main", "mainSessionKey": "agent:main:main"},
            "canvasHostUrl": "http://internal-host",
            "authMode": "token",
        })
        self.assertEqual(summary["method_count"], 2)
        self.assertEqual(summary["event_count"], 2)
        self.assertTrue(summary["server_conn_id_present"])
        self.assertNotIn("secret-conn", str(summary))
        self.assertTrue(summary["canvas_host_url_present"])

    def test_openclaw_protocol_endpoint(self):
        resp = TestClient(app).get("/api/openclaw/protocol")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("methods", payload)
        self.assertIn("agent", payload["methods"])
        self.assertIn("event_hints", payload)


class OpenClawEventSummaryTests(unittest.TestCase):
    def test_agent_event_summary_keeps_schema_not_text(self):
        from mimo2api.openclaw_protocol import summarize_openclaw_event

        summary = summarize_openclaw_event({
            "type": "event",
            "event": "agent",
            "payload": {
                "runId": "run-1",
                "stream": "assistant",
                "sessionKey": "agent:main:main",
                "seq": 2,
                "ts": 123,
                "data": {"delta": "secret text", "text": "secret text"},
            },
            "seq": 9,
        })
        self.assertEqual(summary["run_id"], "run-1")
        self.assertEqual(summary["stream"], "assistant")
        self.assertEqual(summary["text_len"], len("secret text"))
        self.assertNotIn("secret text", str(summary))

    def test_tool_event_summary_includes_safe_schema_fields(self):
        from mimo2api.openclaw_protocol import summarize_openclaw_event

        summary = summarize_openclaw_event({
            "type": "event",
            "event": "agent",
            "payload": {
                "runId": "run-1",
                "stream": "tool",
                "sessionKey": "agent:main:main",
                "seq": 3,
                "ts": 123,
                "data": {
                    "phase": "start",
                    "name": "exec",
                    "toolCallId": "call-secret",
                    "args": {"cmd": "secret command"},
                },
            },
        })
        self.assertEqual(summary["tool_name"], "exec")
        self.assertTrue(summary["tool_call_id_present"])
        self.assertEqual(summary["args_keys"], ["cmd"])
        self.assertNotIn("secret command", str(summary))
        self.assertNotIn("call-secret", str(summary))
