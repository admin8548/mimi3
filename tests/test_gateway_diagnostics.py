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
        old_cooldown_reasons = dict(state.client_cooldown_reasons)
        old_index = state.current_client_index
        try:
            state.active_clients[:] = [legacy, uid_node]
            state.client_uids.clear()
            state.client_uids[id(uid_node)] = "uid-1"
            state.client_cooldowns.clear()
            state.client_cooldown_reasons.clear()
            state.current_client_index = 0
            self.assertIs(get_next_client(), uid_node)
        finally:
            state.active_clients[:] = old_clients
            state.client_uids.clear()
            state.client_uids.update(old_uids)
            state.client_cooldowns.clear()
            state.client_cooldowns.update(old_cooldowns)
            state.client_cooldown_reasons.clear()
            state.client_cooldown_reasons.update(old_cooldown_reasons)
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
        old_cooldown_reasons = dict(state.client_cooldown_reasons)
        old_index = state.current_client_index
        try:
            state.active_clients[:] = [legacy]
            state.client_uids.clear()
            state.client_cooldowns.clear()
            state.client_cooldown_reasons.clear()
            state.current_client_index = 0
            self.assertIsNone(get_next_client())
        finally:
            state.active_clients[:] = old_clients
            state.client_uids.clear()
            state.client_uids.update(old_uids)
            state.client_cooldowns.clear()
            state.client_cooldowns.update(old_cooldowns)
            state.client_cooldown_reasons.clear()
            state.client_cooldown_reasons.update(old_cooldown_reasons)
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
        self.assertIn("allow-once", classify_method("exec.approval.resolve")["parameter_hint"]["observed_decisions"])
        self.assertEqual(classify_method("browser.request")["parameter_hint"]["required"], ["method", "path"])
        self.assertIn("/tabs", classify_method("browser.request")["parameter_hint"]["observed_paths"])
        self.assertIn("/snapshot", classify_method("browser.request")["parameter_hint"]["observed_paths"])
        self.assertEqual(
            classify_method("browser.request")["parameter_hint"]["body_schemas"]["/navigate"]["body"]["url"],
            "about:blank",
        )
        self.assertEqual(classify_method("agents.files.get")["parameter_hint"]["required"], ["agentId", "name"])
        self.assertEqual(classify_method("cron.add")["parameter_hint"]["required"], ["name", "schedule", "sessionTarget", "payload"])
        self.assertEqual(classify_method("cron.add")["parameter_hint"]["known_good"]["delivery"]["mode"], "none")
        self.assertEqual(classify_method("cron.run")["parameter_hint"]["observed_finished"]["delivery_none"]["status"], "ok")
        self.assertEqual(classify_method("config.patch")["parameter_hint"]["required"], ["raw", "baseHash"])
        self.assertEqual(classify_method("node.pair.verify")["parameter_hint"]["required"], ["nodeId", "token"])
        self.assertEqual(classify_method("node.invoke")["parameter_hint"]["required"], ["nodeId", "command", "idempotencyKey"])
        self.assertIn("system.which", classify_method("node.invoke")["parameter_hint"]["meaning"])
        self.assertEqual(classify_method("node.invoke")["parameter_hint"]["official_node_client"]["client_id"], "node-host")
        self.assertEqual(classify_method("node.invoke")["parameter_hint"]["verified_closed_loop"]["uid"], "6875021188")
        self.assertIn("system.run", classify_method("node.invoke")["parameter_hint"]["verified_closed_loop"]["commands"])
        self.assertEqual(
            classify_method("node.invoke")["parameter_hint"]["verified_closed_loop"]["commands"]["system.run"]["result_shape"]["payload"]["stdout"],
            "mimo2api-system-run-ok\n",
        )
        self.assertEqual(
            classify_method("node.invoke")["parameter_hint"]["verified_closed_loop"]["commands"]["browser.proxy"]["params"]["path"],
            "/profiles",
        )
        self.assertEqual(classify_method("sessions.compact")["parameter_hint"]["required"], ["key"])
        self.assertEqual(classify_event("agent")["hint"]["streams"]["assistant"].startswith("模型文本流"), True)
        self.assertIn("request", classify_event("exec.approval.requested")["hint"]["payload_fields"])
        self.assertIn("sessionKey", classify_event("cron")["hint"]["payload_fields"])
        self.assertIn("deliveryStatus", classify_event("cron")["hint"]["payload_fields"])

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


class OpenClawSessionSelectionTests(unittest.TestCase):
    def test_prefers_canonical_main_over_cron_session(self):
        from mimo2api.manager import choose_openclaw_session_key

        sessions = [
            {"key": "agent:main:cron:job:run:session"},
            {"key": "agent:main:main"},
        ]
        self.assertEqual(choose_openclaw_session_key(sessions), "agent:main:main")

    def test_falls_back_to_first_non_cron_session(self):
        from mimo2api.manager import choose_openclaw_session_key

        sessions = [
            {"key": "agent:main:cron:job:run:session"},
            {"key": "agent:main:custom"},
        ]
        self.assertEqual(choose_openclaw_session_key(sessions), "agent:main:custom")

    def test_uses_fallback_when_no_sessions(self):
        from mimo2api.manager import choose_openclaw_session_key

        self.assertEqual(choose_openclaw_session_key([], fallback="agent:main:main"), "agent:main:main")


class GatewayStabilityTests(unittest.TestCase):
    def test_legacy_reject_logging_is_rate_limited(self):
        from mimo2api import web_service

        old_last = web_service._legacy_reject_last_log_at
        old_suppressed = web_service._legacy_reject_suppressed
        old_interval = web_service.LEGACY_REJECT_LOG_INTERVAL_SECONDS
        try:
            web_service._legacy_reject_last_log_at = 0.0
            web_service._legacy_reject_suppressed = 0
            web_service.LEGACY_REJECT_LOG_INTERVAL_SECONDS = 30

            self.assertEqual(web_service.should_log_legacy_reject(now=100.0), (True, 0))
            self.assertEqual(web_service.should_log_legacy_reject(now=101.0), (False, 0))
            self.assertEqual(web_service.should_log_legacy_reject(now=102.0), (False, 0))
            self.assertEqual(web_service.should_log_legacy_reject(now=131.0), (True, 2))
        finally:
            web_service._legacy_reject_last_log_at = old_last
            web_service._legacy_reject_suppressed = old_suppressed
            web_service.LEGACY_REJECT_LOG_INTERVAL_SECONDS = old_interval


class ResponsesConverterStabilityTests(unittest.TestCase):
    def test_sse_event_does_not_mutate_input_payload(self):
        from mimo2api.responses_converter import _sse_event

        payload = {"response": {"status": "in_progress"}}
        event = _sse_event("response.created", payload)
        self.assertIn("response.created", event)
        self.assertNotIn("type", payload)


class ConfigParsingTests(unittest.TestCase):
    def test_invalid_numeric_env_falls_back_and_clamps(self):
        import os
        from mimo2api.config import get_env_float, get_env_int

        old_int = os.environ.get("MIMO_TEST_INT")
        old_float = os.environ.get("MIMO_TEST_FLOAT")
        try:
            os.environ["MIMO_TEST_INT"] = "bad"
            os.environ["MIMO_TEST_FLOAT"] = "bad"
            self.assertEqual(get_env_int("MIMO_TEST_INT", 7, min_value=10), 10)
            self.assertEqual(get_env_float("MIMO_TEST_FLOAT", 1.5, min_value=2.0), 2.0)
        finally:
            if old_int is None:
                os.environ.pop("MIMO_TEST_INT", None)
            else:
                os.environ["MIMO_TEST_INT"] = old_int
            if old_float is None:
                os.environ.pop("MIMO_TEST_FLOAT", None)
            else:
                os.environ["MIMO_TEST_FLOAT"] = old_float

    def test_invalid_bool_env_falls_back(self):
        import os
        from mimo2api.config import get_env_bool

        old_value = os.environ.get("MIMO_TEST_BOOL")
        try:
            os.environ["MIMO_TEST_BOOL"] = "maybe"
            self.assertTrue(get_env_bool("MIMO_TEST_BOOL", True))
        finally:
            if old_value is None:
                os.environ.pop("MIMO_TEST_BOOL", None)
            else:
                os.environ["MIMO_TEST_BOOL"] = old_value


class DispatchObservabilityTests(unittest.TestCase):
    def test_stats_exposes_node_cooldown_reason(self):
        import time
        from mimo2api.gateway_state import state
        from mimo2api.metrics_store import build_gateway_stats

        class ClientInfo:
            host = "127.0.0.1"

        class DummyWs:
            client = ClientInfo()

        ws = DummyWs()
        old_clients = list(state.active_clients)
        old_uids = dict(state.client_uids)
        old_cooldowns = dict(state.client_cooldowns)
        old_cooldown_reasons = dict(state.client_cooldown_reasons)
        old_ws_to_req_ids = dict(state.ws_to_req_ids)
        try:
            state.active_clients[:] = [ws]
            state.client_uids.clear()
            state.client_uids[id(ws)] = "uid-stats"
            state.client_cooldowns.clear()
            state.client_cooldowns[id(ws)] = time.time() + 60
            state.client_cooldown_reasons.clear()
            state.client_cooldown_reasons[id(ws)] = "401 Unauthorized"
            state.ws_to_req_ids.clear()

            stats = build_gateway_stats(background_tasks_count=0)
            self.assertEqual(stats["nodes"][0]["cooldown_reason"], "401 Unauthorized")
            self.assertGreater(stats["nodes"][0]["cooldown_remaining_seconds"], 0)
        finally:
            state.active_clients[:] = old_clients
            state.client_uids.clear()
            state.client_uids.update(old_uids)
            state.client_cooldowns.clear()
            state.client_cooldowns.update(old_cooldowns)
            state.client_cooldown_reasons.clear()
            state.client_cooldown_reasons.update(old_cooldown_reasons)
            state.ws_to_req_ids.clear()
            state.ws_to_req_ids.update(old_ws_to_req_ids)


class ResponsesToolCompatibilityTests(unittest.TestCase):
    def test_convert_request_accepts_responses_and_chat_style_function_tools(self):
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": "hi",
            "tools": [
                {"type": "function", "name": "lookup", "description": "Lookup", "parameters": {"type": "object"}},
                {"type": "function", "function": {"name": "chat_style", "parameters": {"type": "object"}}},
                {"type": "web_search_preview"},
                {"type": "function", "parameters": {"type": "object"}},
            ],
        })

        self.assertEqual([tool["function"]["name"] for tool in converted["tools"]], ["lookup", "chat_style"])
        self.assertEqual(converted["messages"][0]["content"], "hi")
