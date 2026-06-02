import unittest

from fastapi.testclient import TestClient

from mimo2api.web_service import app, build_route_diagnostics


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

