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


class ModelMappingValidationTests(unittest.TestCase):
    def test_model_mapping_requires_string_to_string_entries(self):
        from mimo2api.web_service import normalize_model_mapping

        self.assertEqual(normalize_model_mapping({"gpt-test": "mimo-v2.5"}), {"gpt-test": "mimo-v2.5"})
        self.assertEqual(normalize_model_mapping({" gpt-test ": " mimo-v2.5 "}), {"gpt-test": "mimo-v2.5"})
        self.assertIsNone(normalize_model_mapping(["bad"]))
        self.assertIsNone(normalize_model_mapping({"": "mimo-v2.5"}))
        self.assertIsNone(normalize_model_mapping({"gpt-test": ""}))
        self.assertIsNone(normalize_model_mapping({"gpt-test": 123}))

    def test_model_mapping_put_rejects_invalid_schema(self):
        resp = TestClient(app).put("/api/model_mapping", json={"gpt-test": 123})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("映射必须", resp.json()["error"])


class UserIdValidationTests(unittest.TestCase):
    def test_user_id_validation_rejects_path_like_values(self):
        from mimo2api.ui_router import is_valid_user_id

        self.assertTrue(is_valid_user_id("6875021188"))
        self.assertTrue(is_valid_user_id("user_1.test-2"))
        self.assertFalse(is_valid_user_id("../evil"))
        self.assertFalse(is_valid_user_id(""))
        self.assertFalse(is_valid_user_id("x" * 129))


class DispatchPoolStatsTests(unittest.TestCase):
    def test_dispatch_pool_explains_preferred_uid_fallback(self):
        import os
        import time
        from mimo2api.gateway_state import state
        from mimo2api.metrics_store import build_gateway_stats

        class ClientInfo:
            host = "127.0.0.1"

        class DummyWs:
            client = ClientInfo()

        preferred = DummyWs()
        other_uid = DummyWs()
        legacy = DummyWs()
        old_env_preferred = os.environ.get("MIMO_PREFERRED_UID")
        old_env_legacy = os.environ.get("MIMO_ALLOW_LEGACY_WS_FALLBACK")
        old_clients = list(state.active_clients)
        old_uids = dict(state.client_uids)
        old_cooldowns = dict(state.client_cooldowns)
        old_cooldown_reasons = dict(state.client_cooldown_reasons)
        old_ws_to_req_ids = dict(state.ws_to_req_ids)
        try:
            os.environ["MIMO_PREFERRED_UID"] = "preferred"
            os.environ["MIMO_ALLOW_LEGACY_WS_FALLBACK"] = "false"
            state.active_clients[:] = [preferred, other_uid, legacy]
            state.client_uids.clear()
            state.client_uids[id(preferred)] = "preferred"
            state.client_uids[id(other_uid)] = "other"
            state.client_cooldowns.clear()
            state.client_cooldowns[id(preferred)] = time.time() + 60
            state.client_cooldown_reasons.clear()
            state.ws_to_req_ids.clear()

            stats = build_gateway_stats(background_tasks_count=0)
            self.assertEqual(stats["available_clients"], 2)
            self.assertEqual(stats["dispatchable_clients"], 1)
            self.assertEqual(stats["dispatch_pool"]["effective_pool"], "uid")
            self.assertEqual(stats["dispatch_pool"]["fallback_reason"], "preferred_uid_unavailable")
            self.assertEqual(stats["dispatch_pool"]["available_uid_clients"], 1)
            self.assertEqual(stats["dispatch_pool"]["available_legacy_clients"], 1)
            self.assertEqual(stats["preferred_uid"]["available_count"], 0)
            self.assertTrue(stats["preferred_uid"]["fallback_active"])
        finally:
            if old_env_preferred is None:
                os.environ.pop("MIMO_PREFERRED_UID", None)
            else:
                os.environ["MIMO_PREFERRED_UID"] = old_env_preferred
            if old_env_legacy is None:
                os.environ.pop("MIMO_ALLOW_LEGACY_WS_FALLBACK", None)
            else:
                os.environ["MIMO_ALLOW_LEGACY_WS_FALLBACK"] = old_env_legacy
            state.active_clients[:] = old_clients
            state.client_uids.clear()
            state.client_uids.update(old_uids)
            state.client_cooldowns.clear()
            state.client_cooldowns.update(old_cooldowns)
            state.client_cooldown_reasons.clear()
            state.client_cooldown_reasons.update(old_cooldown_reasons)
            state.ws_to_req_ids.clear()
            state.ws_to_req_ids.update(old_ws_to_req_ids)


class ResponsesStreamingCompatibilityTests(unittest.TestCase):
    def test_stream_converter_emits_in_progress_and_output_text_done(self):
        import json
        from mimo2api.responses_converter import ResponsesStreamConverter

        converter = ResponsesStreamConverter(model="mimo-v2.5")
        events = []
        events.extend(converter.process_chunk("data: " + json.dumps({
            "choices": [{"delta": {"role": "assistant", "content": "hello"}}]
        })))
        events.extend(converter.process_chunk("data: " + json.dumps({
            "choices": [{"delta": {}, "finish_reason": "stop"}]
        })))
        events.extend(converter.process_chunk("data: [DONE]"))
        joined = "".join(events)
        self.assertIn("event: response.in_progress", joined)
        self.assertIn("event: response.output_text.done", joined)
        self.assertIn('"text": "hello"', joined)

    def test_response_input_image_url_object_is_normalized(self):
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_image", "image_url": {"url": "data:image/png;base64,abc"}}],
            }],
        })
        content = converted["messages"][0]["content"]
        self.assertEqual(content[0]["image_url"]["url"], "data:image/png;base64,abc")


class UserStoreTests(unittest.TestCase):
    def test_load_user_records_reports_invalid_files(self):
        import json
        import tempfile
        from pathlib import Path
        from mimo2api.user_store import load_user_records

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "user_good.json").write_text(json.dumps({
                "userId": "u1",
                "serviceToken": "st",
                "xiaomichatbot_ph": "ph",
            }), "utf-8")
            (root / "user_bad_json.json").write_text("{bad", "utf-8")
            (root / "user_missing.json").write_text(json.dumps({"userId": "u2"}), "utf-8")
            (root / "ignore.json").write_text("{}", "utf-8")

            users, invalid = load_user_records(td)
            self.assertEqual([u["userId"] for u in users], ["u1"])
            self.assertEqual(len(invalid), 2)
            self.assertEqual({item["file"] for item in invalid}, {"user_bad_json.json", "user_missing.json"})

    def test_api_users_list_returns_invalid_count(self):
        import json
        import tempfile
        from pathlib import Path
        import mimo2api.ui_router as ui_router

        old_users_dir = ui_router.USERS_DIR
        old_fetch_user_status = ui_router.fetch_user_status

        async def fake_fetch_user_status(data):
            return {**data, "claw_status": "AVAILABLE", "remain_sec": 123}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "user_good.json").write_text(json.dumps({
                "userId": "u1",
                "serviceToken": "st",
                "xiaomichatbot_ph": "ph",
                "name": "User One",
            }), "utf-8")
            (root / "user_bad.json").write_text(json.dumps({"userId": "../bad"}), "utf-8")
            try:
                ui_router.USERS_DIR = td
                ui_router.fetch_user_status = fake_fetch_user_status
                resp = TestClient(app).get("/api/users/list")
                self.assertEqual(resp.status_code, 200)
                payload = resp.json()
                self.assertEqual(len(payload["users"]), 1)
                self.assertEqual(payload["users"][0]["userId"], "u1")
                self.assertEqual(payload["invalid_count"], 1)
                self.assertEqual(payload["invalid_users"][0]["file"], "user_bad.json")
            finally:
                ui_router.USERS_DIR = old_users_dir
                ui_router.fetch_user_status = old_fetch_user_status


class RouteDiagnosticsConfigTests(unittest.TestCase):
    def test_route_diagnostics_includes_config_summary(self):
        diagnostics = build_route_diagnostics()
        self.assertIn("config", diagnostics)
        self.assertIn("model_mapping_path", diagnostics["config"])
        self.assertIn("allow_legacy_ws_fallback", diagnostics["config"])
        self.assertIn("max_pending_queues", diagnostics["config"])


class ErrorDiagnosticsTests(unittest.TestCase):
    def test_api_errors_can_filter_by_category(self):
        from mimo2api.gateway_state import state
        from mimo2api.web_service import record_error

        old_errors = list(state.recent_errors)
        try:
            state.recent_errors.clear()
            record_error("/v1/test", 502, "upstream bad", category="upstream")
            record_error("/v1/test", 400, "request bad", category="request_validation")

            resp = TestClient(app).get("/api/errors?category=upstream")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(payload["category"], "upstream")
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["errors"][0]["category"], "upstream")
        finally:
            state.recent_errors.clear()
            state.recent_errors.extend(old_errors)


class ResponsesBoundaryCompatibilityTests(unittest.TestCase):
    def test_function_call_output_history_is_stringified_as_tool_message(self):
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": {"q": "x"}},
                {"type": "function_call_output", "call_id": "call_1", "output": {"result": 7}},
            ],
        })
        self.assertEqual(converted["messages"][0]["role"], "assistant")
        self.assertEqual(converted["messages"][0]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(converted["messages"][0]["tool_calls"][0]["function"]["arguments"], '{"q": "x"}')
        self.assertEqual(converted["messages"][1]["role"], "tool")
        self.assertEqual(converted["messages"][1]["tool_call_id"], "call_1")
        self.assertEqual(converted["messages"][1]["content"], '{"result": 7}')

    def test_tool_choice_and_max_output_tokens_are_mapped(self):
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": "hi",
            "max_output_tokens": 123,
            "tool_choice": {"type": "function", "name": "lookup"},
            "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
        })
        self.assertEqual(converted["max_tokens"], 123)
        self.assertEqual(converted["tool_choice"], {"type": "function", "function": {"name": "lookup"}})


class ManagerObservabilityTests(unittest.TestCase):
    def test_load_all_users_updates_manager_status(self):
        import json
        import tempfile
        from pathlib import Path
        import mimo2api.manager as manager
        from mimo2api.gateway_state import state
        from mimo2api.metrics_store import build_gateway_stats

        old_users_dir = manager.USERS_DIR
        old_status = dict(state.manager_status)
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "user_good.json").write_text(json.dumps({
                    "userId": "u1",
                    "serviceToken": "st",
                    "xiaomichatbot_ph": "ph",
                }), "utf-8")
                (root / "user_bad.json").write_text("{bad", "utf-8")
                manager.USERS_DIR = td

                users = manager.load_all_users()
                self.assertEqual(list(users.keys()), ["u1"])
                self.assertEqual(state.manager_status["user_files"]["valid_count"], 1)
                self.assertEqual(state.manager_status["user_files"]["invalid_count"], 1)
                self.assertEqual(build_gateway_stats(0)["manager"]["user_files"]["valid_count"], 1)
        finally:
            manager.USERS_DIR = old_users_dir
            state.manager_status.clear()
            state.manager_status.update(old_status)


class AdminErrorCompatibilityTests(unittest.TestCase):
    def test_user_add_invalid_uid_keeps_detail_and_adds_error_object(self):
        resp = TestClient(app).post("/api/users/add", json={
            "raw_text": 'userId="bad!"; serviceToken="st"; xiaomichatbot_ph="ph"'
        })
        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertIn("detail", payload)
        self.assertEqual(payload["error"]["code"], 400)
        self.assertEqual(payload["error"]["type"], "invalid_request_error")
