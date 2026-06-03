import asyncio
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from mimo2api.web_service import app


def write_user(users_dir: Path, uid: str, *, token: str = "st", ph: str = "ph") -> None:
    users_dir.mkdir(parents=True, exist_ok=True)
    (users_dir / f"user_{uid}.json").write_text(
        json.dumps(
            {
                "userId": uid,
                "serviceToken": token,
                "xiaomichatbot_ph": ph,
                "name": f"User {uid}",
            }
        ),
        "utf-8",
    )


class FakeDiagnosticsClient:
    calls = []
    connect_calls = 0
    close_calls = 0
    fail_sections = set()

    def __init__(self, ph, cookies, logger_obj, uid=""):
        self.uid = uid
        self.ph = ph
        self.cookies = cookies

    async def connect(self, wait_available=True, initialize_context=True):
        type(self).connect_calls += 1
        type(self).calls.append(("connect", wait_available, initialize_context))
        return True

    async def request(self, method, params=None, timeout=30):
        params = params or {}
        type(self).calls.append((method, params))
        if method in type(self).fail_sections:
            raise RuntimeError(f"{method} boom")
        if method == "browser.request":
            path = params.get("path")
            if params.get("method") == "POST" and path == "/navigate":
                return {"status": "navigated", "url": (params.get("body") or {}).get("url")}
            if path == "/profiles":
                return {"profiles": [{"id": "default"}]}
            if path == "/tabs":
                return {"tabs": [{"id": "tab1", "wsUrl": "ws://example/?token=secret"}]}
            if path == "/snapshot":
                return {"snapshot": {"text": "page text", "token": "secret"}}
            return {"path": path, "wsUrl": "ws://example/?token=secret"}
        if method == "sessions.list":
            return {"sessions": [{"key": "agent:main:main", "title": "Main"}, {"key": "agent:main:custom"}]}
        if method == "sessions.preview":
            return {"previews": [{"key": key, "text": "preview"} for key in params.get("keys", [])]}
        if method == "sessions.compact":
            return {"status": "compacted", "key": params.get("key")}
        if method == "cron.status":
            return {"running": True}
        if method == "cron.list":
            return {"jobs": [{"id": "job1", "name": "probe"}]}
        if method == "cron.runs":
            return {"runs": [{"id": "run1", "status": "ok"}, {"id": "run2", "status": "error"}]}
        if method == "cron.run":
            return {"status": "enqueued", "id": params.get("id"), "runId": "run1"}
        if method == "config.get":
            return {"raw": "setting=true", "baseHash": "hash", "token": "secret"}
        if method == "config.schema":
            return {"schema": {"type": "object"}}
        if method == "agents.files.list":
            return {"files": [{"name": "AGENTS.md"}, {"name": "SOUL.md"}]}
        if method == "agents.files.get":
            return {"name": params.get("name"), "content": "hello", "secret": "hidden"}
        return {"method": method, "token": "secret", "value": 1}

    async def close(self):
        type(self).close_calls += 1


class OpenClawDiagnosticsAllowlistTests(unittest.TestCase):
    def test_denies_mutating_methods(self):
        from mimo2api.openclaw_diagnostics import DiagnosticsRPCDenied, validate_readonly_rpc

        for method in ("agent", "chat.send", "node.invoke", "cron.add", "config.patch", "agents.files.set", "sessions.compact", "sessions.delete"):
            with self.assertRaises(DiagnosticsRPCDenied):
                validate_readonly_rpc(method, {})

    def test_browser_request_allowlist(self):
        from mimo2api.openclaw_diagnostics import DiagnosticsRPCDenied, validate_readonly_rpc

        for path in ("/", "/profiles", "/tabs"):
            validate_readonly_rpc("browser.request", {"method": "GET", "path": path})
        validate_readonly_rpc("sessions.preview", {"keys": ["agent:main:main"]})
        validate_readonly_rpc("config.get", {})
        validate_readonly_rpc("config.schema", {})
        validate_readonly_rpc("agents.files.list", {"agentId": "main"})
        validate_readonly_rpc("agents.files.get", {"agentId": "main", "name": "AGENTS.md"})
        with self.assertRaises(DiagnosticsRPCDenied):
            validate_readonly_rpc("browser.request", {"method": "GET", "path": "/snapshot"})
        validate_readonly_rpc("browser.request", {"method": "GET", "path": "/snapshot"}, include_snapshot=True)
        with self.assertRaises(DiagnosticsRPCDenied):
            validate_readonly_rpc("browser.request", {"method": "POST", "path": "/start"})
        with self.assertRaises(DiagnosticsRPCDenied):
            validate_readonly_rpc("browser.request", {"method": "GET", "path": "/navigate"})


class OpenClawDiagnosticsSelectionTests(unittest.TestCase):
    def test_uid_selection_priority(self):
        from mimo2api.openclaw_diagnostics import select_user_record

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_user(root, "u1")
            write_user(root, "u2")
            selected, errors = select_user_record("u2", users_dir=root, preferred_uid="u1")
            self.assertFalse(errors)
            self.assertEqual(selected["userId"], "u2")

    def test_preferred_uid_then_first_fallback(self):
        from mimo2api.openclaw_diagnostics import select_user_record

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_user(root, "u1")
            write_user(root, "u2")
            selected, _ = select_user_record(None, users_dir=root, preferred_uid="u2")
            self.assertEqual(selected["userId"], "u2")
            selected, _ = select_user_record(None, users_dir=root, preferred_uid="missing")
            self.assertEqual(selected["userId"], "u1")

    def test_no_valid_users(self):
        from mimo2api.openclaw_diagnostics import select_user_record

        with tempfile.TemporaryDirectory() as td:
            selected, errors = select_user_record(None, users_dir=td)
            self.assertIsNone(selected)
            self.assertEqual(errors[0]["message"], "no valid users")


class OpenClawDiagnosticsEndpointTests(unittest.TestCase):
    def setUp(self):
        from mimo2api.openclaw_diagnostics import clear_diagnostics_cache

        clear_diagnostics_cache()
        FakeDiagnosticsClient.calls = []
        FakeDiagnosticsClient.connect_calls = 0
        FakeDiagnosticsClient.close_calls = 0
        FakeDiagnosticsClient.fail_sections = set()

    def test_sections_aggregate_and_redact(self):
        from mimo2api.openclaw_diagnostics import build_openclaw_diagnostics

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                build_openclaw_diagnostics(
                    uid="u1",
                    sections="health,browser",
                    refresh=True,
                    include_snapshot=False,
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["selected_uid"], "u1")
        self.assertTrue(payload["sections"]["health"]["ok"])
        self.assertEqual(payload["sections"]["health"]["payload"]["token"], "<redacted>")
        self.assertNotIn("/snapshot", payload["sections"]["browser"]["payload"])
        self.assertEqual(FakeDiagnosticsClient.calls[0], ("connect", False, False))
        self.assertEqual(FakeDiagnosticsClient.close_calls, 1)

    def test_section_failure_does_not_fail_other_sections(self):
        from mimo2api.openclaw_diagnostics import build_openclaw_diagnostics

        FakeDiagnosticsClient.fail_sections = {"status"}
        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                build_openclaw_diagnostics(
                    uid="u1",
                    sections="health,status",
                    refresh=True,
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["sections"]["health"]["ok"])
        self.assertFalse(payload["sections"]["status"]["ok"])
        self.assertEqual(payload["errors"][0]["section"], "status")

    def test_refresh_bypasses_cache(self):
        from mimo2api.openclaw_diagnostics import build_openclaw_diagnostics

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            kwargs = dict(uid="u1", sections="health", users_dir=td, client_factory=FakeDiagnosticsClient)
            first = asyncio.run(build_openclaw_diagnostics(**kwargs))
            second = asyncio.run(build_openclaw_diagnostics(**kwargs))
            third = asyncio.run(build_openclaw_diagnostics(**kwargs, refresh=True))

        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertFalse(third["cache"]["hit"])
        self.assertEqual(FakeDiagnosticsClient.connect_calls, 2)

    def test_endpoint_uses_composite_api_shape(self):
        expected = {
            "ok": True,
            "generated_at": 1,
            "selected_uid": "u1",
            "cache": {"hit": False, "ttl_seconds": 30},
            "sections": {"health": {"ok": True, "payload": {}}},
            "errors": [],
        }

        async def fake_builder(**kwargs):
            self.assertEqual(kwargs["uid"], "u1")
            self.assertEqual(kwargs["sections"], "health")
            self.assertTrue(kwargs["refresh"])
            return expected

        with patch("mimo2api.web_service.build_openclaw_diagnostics", fake_builder):
            resp = TestClient(app).get("/api/openclaw/diagnostics?uid=u1&sections=health&refresh=true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), expected)


class OpenClawObservabilityTests(unittest.TestCase):
    def setUp(self):
        from mimo2api.gateway_state import state

        state.openclaw_features_by_uid.clear()
        state.recent_agent_runs.clear()
        state.recent_openclaw_events.clear()

    def test_observability_filters_and_sanitizes_runtime_summaries(self):
        from mimo2api.gateway_state import state
        from mimo2api.web_service import build_openclaw_observability

        state.openclaw_features_by_uid["u1"] = {
            "protocol": 3,
            "server_version": "2026.test",
            "method_count": 2,
            "event_count": 1,
            "methods": ["health", "agent"],
            "events": ["agent"],
            "method_categories": {"health": 1, "agent_run": 1},
            "token": "secret",
        }
        state.openclaw_features_by_uid["u2"] = {"protocol": 3, "method_count": 1, "event_count": 0}
        state.recent_agent_runs.append({"uid": "u1", "status": "ok", "tool_seen": True, "duration_ms": 12})
        state.recent_agent_runs.append({"uid": "u2", "status": "failed", "tool_seen": False, "duration_ms": 34})
        state.recent_openclaw_events.append({"uid": "u1", "event": "agent", "stream": "tool"})
        state.recent_openclaw_events.append({"uid": "u2", "event": "chat"})

        payload = build_openclaw_observability(uid="u1", limit=10)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["features"]["count"], 1)
        self.assertIn("u1", payload["features"]["by_uid"])
        self.assertNotIn("u2", payload["features"]["by_uid"])
        self.assertEqual(payload["features"]["by_uid"]["u1"]["token"], "<redacted>")
        self.assertEqual(payload["agent_runs"]["count"], 1)
        self.assertEqual(payload["agent_runs"]["status_counts"], {"ok": 1})
        self.assertEqual(payload["events"]["event_counts"], {"agent": 1})
        self.assertEqual(payload["events"]["stream_counts"], {"tool": 1})

    def test_observability_endpoint_shape(self):
        from mimo2api.gateway_state import state

        state.recent_agent_runs.append({"uid": "u1", "status": "ok"})
        resp = TestClient(app).get("/api/openclaw/observability?uid=u1&limit=5")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("features", payload)
        self.assertIn("agent_runs", payload)
        self.assertIn("events", payload)
        self.assertEqual(payload["filters"]["uid"], "u1")


class OpenClawSessionsOverviewTests(unittest.TestCase):
    def setUp(self):
        FakeDiagnosticsClient.calls = []
        FakeDiagnosticsClient.connect_calls = 0
        FakeDiagnosticsClient.close_calls = 0
        FakeDiagnosticsClient.fail_sections = set()

    def test_sessions_overview_uses_list_and_preview_readonly(self):
        from mimo2api.openclaw_sessions import build_openclaw_sessions_overview

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                build_openclaw_sessions_overview(
                    uid="u1",
                    limit=10,
                    include_preview=True,
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["sessions"]["ok"])
        self.assertEqual(payload["sessions"]["count"], 2)
        self.assertTrue(payload["preview"]["ok"])
        methods = [call[0] for call in FakeDiagnosticsClient.calls]
        self.assertIn("sessions.list", methods)
        self.assertIn("sessions.preview", methods)
        self.assertNotIn("sessions.compact", methods)
        self.assertEqual(FakeDiagnosticsClient.calls[0], ("connect", False, False))
        self.assertEqual(FakeDiagnosticsClient.close_calls, 1)

    def test_sessions_overview_endpoint_shape(self):
        expected = {
            "ok": True,
            "generated_at": 1,
            "selected_uid": "u1",
            "requested": {"limit": 5, "include_preview": True},
            "sessions": {"ok": True, "count": 0, "payload": {}},
            "preview": {"ok": True, "payload": {}},
            "errors": [],
        }

        async def fake_builder(**kwargs):
            self.assertEqual(kwargs["uid"], "u1")
            self.assertEqual(kwargs["limit"], 5)
            self.assertTrue(kwargs["include_preview"])
            return expected

        with patch("mimo2api.web_service.build_openclaw_sessions_overview", fake_builder):
            resp = TestClient(app).get("/api/openclaw/sessions/overview?uid=u1&limit=5&include_preview=true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), expected)


class OpenClawCronOverviewTests(unittest.TestCase):
    def setUp(self):
        FakeDiagnosticsClient.calls = []
        FakeDiagnosticsClient.connect_calls = 0
        FakeDiagnosticsClient.close_calls = 0
        FakeDiagnosticsClient.fail_sections = set()

    def test_cron_overview_uses_readonly_cron_methods(self):
        from mimo2api.openclaw_cron import build_openclaw_cron_overview

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                build_openclaw_cron_overview(
                    uid="u1",
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["job_count"], 1)
        self.assertEqual(payload["summary"]["run_count"], 2)
        self.assertEqual(payload["summary"]["run_status_counts"], {"ok": 1, "error": 1})
        methods = [call[0] for call in FakeDiagnosticsClient.calls]
        self.assertIn("cron.status", methods)
        self.assertIn("cron.list", methods)
        self.assertIn("cron.runs", methods)
        self.assertNotIn("cron.run", methods)
        self.assertNotIn("cron.add", methods)
        self.assertEqual(FakeDiagnosticsClient.calls[0], ("connect", False, False))
        self.assertEqual(FakeDiagnosticsClient.close_calls, 1)

    def test_cron_overview_endpoint_shape(self):
        expected = {
            "ok": True,
            "generated_at": 1,
            "selected_uid": "u1",
            "cron": {},
            "summary": {},
            "errors": [],
        }

        async def fake_builder(**kwargs):
            self.assertEqual(kwargs["uid"], "u1")
            return expected

        with patch("mimo2api.web_service.build_openclaw_cron_overview", fake_builder):
            resp = TestClient(app).get("/api/openclaw/cron/overview?uid=u1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), expected)


class OpenClawBrowserOverviewTests(unittest.TestCase):
    def setUp(self):
        FakeDiagnosticsClient.calls = []
        FakeDiagnosticsClient.connect_calls = 0
        FakeDiagnosticsClient.close_calls = 0
        FakeDiagnosticsClient.fail_sections = set()

    def test_browser_overview_uses_readonly_get_paths_without_snapshot_by_default(self):
        from mimo2api.openclaw_browser import build_openclaw_browser_overview

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                build_openclaw_browser_overview(
                    uid="u1",
                    include_snapshot=False,
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["profile_count"], 1)
        self.assertEqual(payload["summary"]["tab_count"], 1)
        self.assertFalse(payload["summary"]["snapshot_loaded"])
        browser_calls = [call[1]["path"] for call in FakeDiagnosticsClient.calls if call[0] == "browser.request"]
        self.assertEqual(browser_calls, ["/", "/profiles", "/tabs"])
        self.assertIsNone(payload["browser"]["snapshot"])
        self.assertEqual(FakeDiagnosticsClient.calls[0], ("connect", False, False))
        self.assertEqual(FakeDiagnosticsClient.close_calls, 1)

    def test_browser_overview_snapshot_is_explicit(self):
        from mimo2api.openclaw_browser import build_openclaw_browser_overview

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                build_openclaw_browser_overview(
                    uid="u1",
                    include_snapshot=True,
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        browser_calls = [call[1]["path"] for call in FakeDiagnosticsClient.calls if call[0] == "browser.request"]
        self.assertIn("/snapshot", browser_calls)
        self.assertTrue(payload["browser"]["snapshot"]["ok"])
        self.assertEqual(payload["browser"]["snapshot"]["payload"]["snapshot"]["token"], "<redacted>")

    def test_browser_overview_endpoint_shape(self):
        expected = {
            "ok": True,
            "generated_at": 1,
            "selected_uid": "u1",
            "requested": {"include_snapshot": True},
            "browser": {},
            "summary": {},
            "errors": [],
        }

        async def fake_builder(**kwargs):
            self.assertEqual(kwargs["uid"], "u1")
            self.assertTrue(kwargs["include_snapshot"])
            return expected

        with patch("mimo2api.web_service.build_openclaw_browser_overview", fake_builder):
            resp = TestClient(app).get("/api/openclaw/browser/overview?uid=u1&include_snapshot=true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), expected)


class OpenClawConfigFilesOverviewTests(unittest.TestCase):
    def setUp(self):
        FakeDiagnosticsClient.calls = []
        FakeDiagnosticsClient.connect_calls = 0
        FakeDiagnosticsClient.close_calls = 0
        FakeDiagnosticsClient.fail_sections = set()

    def test_config_files_overview_uses_readonly_methods(self):
        from mimo2api.openclaw_config_files import build_openclaw_config_files_overview

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                build_openclaw_config_files_overview(
                    uid="u1",
                    agent_id="main",
                    file_name="AGENTS.md",
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["file_count"], 2)
        self.assertTrue(payload["summary"]["file_loaded"])
        self.assertEqual(payload["config"]["get"]["payload"]["token"], "<redacted>")
        self.assertEqual(payload["agent_files"]["get"]["payload"]["secret"], "<redacted>")
        methods = [call[0] for call in FakeDiagnosticsClient.calls]
        self.assertIn("config.get", methods)
        self.assertIn("config.schema", methods)
        self.assertIn("agents.files.list", methods)
        self.assertIn("agents.files.get", methods)
        self.assertNotIn("config.patch", methods)
        self.assertNotIn("agents.files.set", methods)
        self.assertEqual(FakeDiagnosticsClient.calls[0], ("connect", False, False))
        self.assertEqual(FakeDiagnosticsClient.close_calls, 1)

    def test_config_files_rejects_unsafe_file_name(self):
        from mimo2api.openclaw_config_files import build_openclaw_config_files_overview

        payload = asyncio.run(
            build_openclaw_config_files_overview(
                uid="u1",
                agent_id="main",
                file_name="../secret",
                client_factory=FakeDiagnosticsClient,
            )
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["message"], "invalid file_name")
        self.assertEqual(FakeDiagnosticsClient.calls, [])

    def test_config_files_endpoint_shape(self):
        expected = {
            "ok": True,
            "generated_at": 1,
            "selected_uid": "u1",
            "requested": {"agent_id": "main", "file_name": "AGENTS.md"},
            "config": {},
            "agent_files": {},
            "summary": {},
            "errors": [],
        }

        async def fake_builder(**kwargs):
            self.assertEqual(kwargs["uid"], "u1")
            self.assertEqual(kwargs["agent_id"], "main")
            self.assertEqual(kwargs["file_name"], "AGENTS.md")
            return expected

        with patch("mimo2api.web_service.build_openclaw_config_files_overview", fake_builder):
            resp = TestClient(app).get("/api/openclaw/config-files/overview?uid=u1&agent_id=main&file_name=AGENTS.md")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), expected)


class OpenClawBackupDiffPreviewTests(unittest.TestCase):
    def setUp(self):
        from mimo2api.openclaw_backup_diff import clear_backup_diff_records

        clear_backup_diff_records()
        FakeDiagnosticsClient.calls = []
        FakeDiagnosticsClient.close_calls = 0

    def test_config_backup_diff_preview_reads_config_only(self):
        from mimo2api.openclaw_backup_diff import build_openclaw_backup_diff_preview, list_backup_diff_records

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                build_openclaw_backup_diff_preview(
                    uid="u1",
                    target_type="config",
                    proposed_content="setting=false",
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["backup"]["metadata_only"])
        self.assertTrue(payload["diff"]["changed"])
        methods = [call[0] for call in FakeDiagnosticsClient.calls]
        self.assertIn("config.get", methods)
        self.assertNotIn("config.patch", methods)
        self.assertEqual(list_backup_diff_records()["count"], 1)

    def test_agent_file_backup_diff_preview_reads_file_only(self):
        from mimo2api.openclaw_backup_diff import build_openclaw_backup_diff_preview

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                build_openclaw_backup_diff_preview(
                    uid="u1",
                    target_type="agent_file",
                    agent_id="main",
                    file_name="AGENTS.md",
                    proposed_content="hello",
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["diff"]["changed"])
        methods = [call[0] for call in FakeDiagnosticsClient.calls]
        self.assertIn("agents.files.get", methods)
        self.assertNotIn("agents.files.set", methods)

    def test_backup_diff_endpoint_shape(self):
        expected = {
            "ok": True,
            "generated_at": 1,
            "selected_uid": "u1",
            "target": {"type": "config"},
            "backup": {},
            "diff": {},
            "source_payload": {},
            "errors": [],
        }

        async def fake_builder(**kwargs):
            self.assertEqual(kwargs["uid"], "u1")
            self.assertEqual(kwargs["target_type"], "config")
            self.assertEqual(kwargs["proposed_content"], "x")
            return expected

        with patch("mimo2api.web_service.build_openclaw_backup_diff_preview", fake_builder):
            resp = TestClient(app).post(
                "/api/openclaw/backup-diff/preview",
                json={"uid": "u1", "target_type": "config", "proposed_content": "x"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), expected)


class OpenClawMutationSafetyTests(unittest.TestCase):
    def setUp(self):
        from mimo2api.openclaw_mutation_safety import clear_mutation_safety_state

        clear_mutation_safety_state()
        os.environ.pop("MIMO_OPENCLAW_MUTATIONS_ENABLED", None)

    def tearDown(self):
        os.environ.pop("MIMO_OPENCLAW_MUTATIONS_ENABLED", None)

    def test_mutation_safety_default_disabled_and_preview_audits(self):
        from mimo2api.openclaw_mutation_safety import (
            build_mutation_preview,
            build_mutation_safety_status,
            list_mutation_audit,
        )

        status = build_mutation_safety_status()
        self.assertFalse(status["mutations_enabled"])
        self.assertIn("sessions.compact", status["supported_actions"])

        preview = build_mutation_preview("sessions.compact", uid="u1", params={"key": "agent:main:main", "token": "secret"})
        self.assertTrue(preview["ok"])
        self.assertFalse(preview["would_execute"])
        self.assertFalse(preview["confirmation_available"])
        self.assertEqual(preview["params"]["token"], "<redacted>")

        audit = list_mutation_audit()
        self.assertEqual(audit["count"], 1)
        self.assertEqual(audit["records"][0]["stage"], "preview")

    def test_confirmation_token_not_issued_when_disabled(self):
        from mimo2api.openclaw_mutation_safety import create_confirmation_token

        payload = create_confirmation_token("cron.run", uid="u1", params={"jobId": "job1"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["token"], "")
        self.assertIn("disabled", payload["errors"][0]["message"])

    def test_confirmation_token_can_be_verified_when_enabled(self):
        from mimo2api.openclaw_mutation_safety import create_confirmation_token, verify_confirmation_token

        os.environ["MIMO_OPENCLAW_MUTATIONS_ENABLED"] = "true"
        params = {"jobId": "job1"}
        payload = create_confirmation_token("cron.run", uid="u1", params=params)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["token"])
        self.assertTrue(verify_confirmation_token(payload["token"], action="cron.run", uid="u1", params=params))
        self.assertFalse(verify_confirmation_token(payload["token"], action="cron.run", uid="u1", params=params))

    def test_mutation_safety_endpoints(self):
        resp = TestClient(app).get("/api/openclaw/mutations/safety")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["mutations_enabled"])

        resp = TestClient(app).post(
            "/api/openclaw/mutations/preview",
            json={"action": "browser.navigate", "uid": "u1", "params": {"url": "about:blank"}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

        resp = TestClient(app).get("/api/openclaw/mutations/audit?limit=5")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.json()["count"], 1)


class OpenClawSessionsCompactMutationTests(unittest.TestCase):
    def setUp(self):
        from mimo2api.openclaw_mutation_safety import clear_mutation_safety_state

        clear_mutation_safety_state()
        os.environ.pop("MIMO_OPENCLAW_MUTATIONS_ENABLED", None)
        FakeDiagnosticsClient.calls = []
        FakeDiagnosticsClient.connect_calls = 0
        FakeDiagnosticsClient.close_calls = 0
        FakeDiagnosticsClient.fail_sections = set()

    def tearDown(self):
        os.environ.pop("MIMO_OPENCLAW_MUTATIONS_ENABLED", None)

    def test_sessions_compact_blocked_when_feature_flag_disabled(self):
        from mimo2api.openclaw_session_mutations import execute_sessions_compact

        payload = asyncio.run(
            execute_sessions_compact(
                uid="u1",
                key="agent:main:main",
                confirmation_token="token",
                client_factory=FakeDiagnosticsClient,
            )
        )
        self.assertFalse(payload["ok"])
        self.assertIn("disabled", payload["errors"][0]["message"])


class OpenClawCronRunMutationTests(unittest.TestCase):
    def setUp(self):
        from mimo2api.openclaw_mutation_safety import clear_mutation_safety_state

        clear_mutation_safety_state()
        os.environ.pop("MIMO_OPENCLAW_MUTATIONS_ENABLED", None)
        FakeDiagnosticsClient.calls = []
        FakeDiagnosticsClient.connect_calls = 0
        FakeDiagnosticsClient.close_calls = 0
        FakeDiagnosticsClient.fail_sections = set()

    def tearDown(self):
        os.environ.pop("MIMO_OPENCLAW_MUTATIONS_ENABLED", None)

    def test_cron_run_blocked_when_feature_flag_disabled(self):
        from mimo2api.openclaw_cron_mutations import execute_cron_run

        payload = asyncio.run(
            execute_cron_run(
                uid="u1",
                cron_id="job1",
                confirmation_token="token",
                client_factory=FakeDiagnosticsClient,
            )
        )
        self.assertFalse(payload["ok"])
        self.assertIn("disabled", payload["errors"][0]["message"])


class OpenClawBrowserNavigateMutationTests(unittest.TestCase):
    def setUp(self):
        from mimo2api.openclaw_mutation_safety import clear_mutation_safety_state

        clear_mutation_safety_state()
        os.environ.pop("MIMO_OPENCLAW_MUTATIONS_ENABLED", None)
        FakeDiagnosticsClient.calls = []
        FakeDiagnosticsClient.connect_calls = 0
        FakeDiagnosticsClient.close_calls = 0
        FakeDiagnosticsClient.fail_sections = set()

    def tearDown(self):
        os.environ.pop("MIMO_OPENCLAW_MUTATIONS_ENABLED", None)

    def test_browser_navigate_url_validation(self):
        from mimo2api.openclaw_browser_mutations import validate_navigate_url

        self.assertTrue(validate_navigate_url("https://example.com")[0])
        self.assertTrue(validate_navigate_url("http://example.com/path")[0])
        self.assertTrue(validate_navigate_url("about:blank")[0])
        self.assertFalse(validate_navigate_url("data:text/html,hi")[0])
        self.assertFalse(validate_navigate_url("javascript:alert(1)")[0])
        self.assertFalse(validate_navigate_url("file:///etc/passwd")[0])

    def test_browser_navigate_blocked_when_feature_flag_disabled(self):
        from mimo2api.openclaw_browser_mutations import execute_browser_navigate

        payload = asyncio.run(
            execute_browser_navigate(
                uid="u1",
                url="https://example.com",
                confirmation_token="token",
                client_factory=FakeDiagnosticsClient,
            )
        )
        self.assertFalse(payload["ok"])
        self.assertIn("disabled", payload["errors"][0]["message"])
        self.assertEqual(FakeDiagnosticsClient.calls, [])

    def test_browser_navigate_executes_when_enabled_and_confirmed(self):
        from mimo2api.openclaw_mutation_safety import create_confirmation_token
        from mimo2api.openclaw_browser_mutations import execute_browser_navigate

        os.environ["MIMO_OPENCLAW_MUTATIONS_ENABLED"] = "true"
        params = {"url": "https://example.com"}
        token_payload = create_confirmation_token("browser.navigate", uid="u1", params=params)
        self.assertTrue(token_payload["ok"])

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                execute_browser_navigate(
                    uid="u1",
                    url="https://example.com",
                    confirmation_token=token_payload["token"],
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["payload"]["status"], "navigated")
        browser_calls = [call for call in FakeDiagnosticsClient.calls if call[0] == "browser.request"]
        self.assertEqual(browser_calls[0][1]["method"], "POST")
        self.assertEqual(browser_calls[0][1]["path"], "/navigate")
        self.assertEqual(FakeDiagnosticsClient.close_calls, 1)

    def test_browser_navigate_endpoint_exists_and_blocks_by_default(self):
        resp = TestClient(app).post(
            "/api/openclaw/browser/navigate",
            json={"uid": "u1", "url": "https://example.com", "confirmation_token": "bad"},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["ok"])
        self.assertIn("disabled", payload["errors"][0]["message"])
        self.assertEqual(FakeDiagnosticsClient.calls, [])

    def test_cron_run_requires_valid_confirmation_and_executes_when_enabled(self):
        from mimo2api.openclaw_mutation_safety import create_confirmation_token
        from mimo2api.openclaw_cron_mutations import execute_cron_run

        os.environ["MIMO_OPENCLAW_MUTATIONS_ENABLED"] = "true"
        params = {"id": "job1"}
        token_payload = create_confirmation_token("cron.run", uid="u1", params=params)
        self.assertTrue(token_payload["ok"])

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                execute_cron_run(
                    uid="u1",
                    cron_id="job1",
                    confirmation_token=token_payload["token"],
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["payload"]["status"], "enqueued")
        methods = [call[0] for call in FakeDiagnosticsClient.calls]
        self.assertIn("cron.run", methods)
        self.assertEqual(FakeDiagnosticsClient.close_calls, 1)

    def test_cron_run_endpoint_exists_and_blocks_by_default(self):
        resp = TestClient(app).post(
            "/api/openclaw/cron/run",
            json={"uid": "u1", "id": "job1", "confirmation_token": "bad"},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["ok"])
        self.assertIn("disabled", payload["errors"][0]["message"])
        self.assertEqual(FakeDiagnosticsClient.calls, [])

    def test_sessions_compact_requires_valid_confirmation_and_executes_when_enabled(self):
        from mimo2api.openclaw_mutation_safety import create_confirmation_token
        from mimo2api.openclaw_session_mutations import execute_sessions_compact

        os.environ["MIMO_OPENCLAW_MUTATIONS_ENABLED"] = "true"
        params = {"key": "agent:main:main"}
        token_payload = create_confirmation_token("sessions.compact", uid="u1", params=params)
        self.assertTrue(token_payload["ok"])

        with tempfile.TemporaryDirectory() as td:
            write_user(Path(td), "u1")
            payload = asyncio.run(
                execute_sessions_compact(
                    uid="u1",
                    key="agent:main:main",
                    confirmation_token=token_payload["token"],
                    users_dir=td,
                    client_factory=FakeDiagnosticsClient,
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["payload"]["status"], "compacted")
        methods = [call[0] for call in FakeDiagnosticsClient.calls]
        self.assertIn("sessions.compact", methods)
        self.assertEqual(FakeDiagnosticsClient.close_calls, 1)

    def test_sessions_compact_endpoint_exists_and_blocks_by_default(self):
        resp = TestClient(app).post(
            "/api/openclaw/sessions/compact",
            json={"uid": "u1", "key": "agent:main:main", "confirmation_token": "bad"},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["ok"])
        self.assertIn("disabled", payload["errors"][0]["message"])


class NativeClawClientInitializeContextTests(unittest.TestCase):
    def test_connect_signature_keeps_default_initialize_context_true(self):
        from mimo2api.manager import NativeClawClient

        signature = inspect.signature(NativeClawClient.connect)
        self.assertEqual(signature.parameters["initialize_context"].default, True)

    def test_connect_initialize_context_false_skips_chat_initialization(self):
        from mimo2api import manager

        class FakeWS:
            def __init__(self):
                self.messages = [
                    json.dumps({"type": "event", "event": "connect.challenge", "payload": {}}),
                    json.dumps({"type": "res", "id": "connect-id", "ok": True, "payload": {"type": "hello-ok"}}),
                ]
                self.sent = []

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.messages:
                    return self.messages.pop(0)
                await asyncio.sleep(60)

            async def send(self, payload):
                self.sent.append(payload)

            async def close(self):
                return None

        async def run_case():
            fake_ws = FakeWS()
            client = manager.NativeClawClient("ph", {"serviceToken": "st"}, manager.logger, uid="u1")
            init_calls = {"count": 0}

            async def fake_ticket():
                return "ticket"

            async def fake_init():
                init_calls["count"] += 1

            async def fake_connect(*args, **kwargs):
                return fake_ws

            client._get_ticket = fake_ticket
            client.initialize_chat_context = fake_init
            with patch.object(manager.websockets, "connect", fake_connect):
                ok = await client.connect(wait_available=False, initialize_context=False)
            await client.close()
            return ok, init_calls["count"]

        ok, init_count = asyncio.run(run_case())
        self.assertTrue(ok)
        self.assertEqual(init_count, 0)


class WebUIOpenClawStaticTests(unittest.TestCase):
    def test_webui_contains_openclaw_tab_controls_and_cards(self):
        html = Path("mimo2api/webui.html").read_text("utf-8")
        self.assertIn("tabBtn-openclaw", html)
        self.assertIn("openclawUidSelect", html)
        self.assertIn("openclawRefreshBtn", html)
        self.assertIn("openclawCards", html)
        self.assertIn("/api/openclaw/diagnostics", html)
        self.assertIn("openclawFeatureMatrix", html)
        self.assertIn("openclawAgentRuns", html)
        self.assertIn("/api/openclaw/observability", html)
        self.assertIn("openclawSessionsOverview", html)
        self.assertIn("/api/openclaw/sessions/overview", html)
        self.assertIn("openclawCronOverview", html)
        self.assertIn("/api/openclaw/cron/overview", html)
        self.assertIn("openclawBrowserOverview", html)
        self.assertIn("/api/openclaw/browser/overview", html)
        self.assertIn("openclawConfigFilesOverview", html)
        self.assertIn("/api/openclaw/config-files/overview", html)
        self.assertIn("openclawMutationSafety", html)
        self.assertIn("/api/openclaw/mutations/safety", html)
        self.assertIn("/api/openclaw/mutations/preview", html)
        self.assertIn("openclawCompactResult", html)
        self.assertIn("/api/openclaw/sessions/compact", html)
        self.assertIn("openclawCronRunResult", html)
        self.assertIn("/api/openclaw/cron/run", html)
        self.assertIn("openclawNavigateResult", html)
        self.assertIn("/api/openclaw/browser/navigate", html)
        self.assertIn("openclawBackupDiffPreview", html)
        self.assertIn("/api/openclaw/backup-diff/preview", html)
