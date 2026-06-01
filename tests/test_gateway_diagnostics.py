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


if __name__ == "__main__":
    unittest.main()

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
