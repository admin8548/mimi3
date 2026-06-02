import unittest

from fastapi.testclient import TestClient

from mimo2api.web_service import app, build_route_diagnostics


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


class ModelMappingAdminErrorCompatibilityTests(unittest.TestCase):
    def test_model_mapping_errors_keep_legacy_error_and_add_error_object(self):
        resp = TestClient(app).put("/api/model_mapping", content="{bad", headers={"content-type": "application/json"})
        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertIsInstance(payload["error"], str)
        self.assertEqual(payload["detail"], payload["error"])
        self.assertEqual(payload["error_object"]["code"], 400)
        self.assertEqual(payload["error_object"]["type"], "invalid_request_error")

    def test_model_mapping_delete_not_found_has_error_object(self):
        resp = TestClient(app).delete("/api/model_mapping/__definitely_missing_model__")
        self.assertEqual(resp.status_code, 404)
        payload = resp.json()
        self.assertIsInstance(payload["error"], str)
        self.assertEqual(payload["error_object"]["type"], "not_found")


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

