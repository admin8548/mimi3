import unittest

from fastapi.testclient import TestClient

from mimo2api.web_service import app, build_route_diagnostics


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

