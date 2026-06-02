import unittest

from fastapi.testclient import TestClient

from mimo2api.web_service import app, build_route_diagnostics


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


class UserIdValidationTests(unittest.TestCase):
    def test_user_id_validation_rejects_path_like_values(self):
        from mimo2api.ui_router import is_valid_user_id

        self.assertTrue(is_valid_user_id("6875021188"))
        self.assertTrue(is_valid_user_id("user_1.test-2"))
        self.assertFalse(is_valid_user_id("../evil"))
        self.assertFalse(is_valid_user_id(""))
        self.assertFalse(is_valid_user_id("x" * 129))


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

