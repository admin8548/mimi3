import asyncio
import json
import time
from collections import deque
from typing import Any, Dict, List
from fastapi import WebSocket

METRICS_SNAPSHOT_PATH = None  # 延迟初始化，在 metrics_store 中设置

class GatewayState:
    def __init__(self):
        self.active_clients: List[WebSocket] = []
        self.client_uids: Dict[int, str] = {}  # id(ws) -> uid from /ws?uid=...
        self.client_connected_at: Dict[int, float] = {}  # id(ws) -> gateway-side connect timestamp
        self.pending_queues: Dict[str, asyncio.Queue] = {}
        self.ws_to_req_ids: Dict[int, set] = {}  # id(ws) -> {req_id, ...}
        self.req_id_to_ws_id: Dict[str, int] = {}
        self.req_id_timestamps: Dict[str, float] = {}
        self.current_client_index: int = 0
        self.rebuild_event: asyncio.Event = asyncio.Event()
        self.client_cooldowns: Dict[int, float] = {}
        self.client_cooldown_reasons: Dict[int, str] = {}
        # id(ws) -> bridge health state.  New bridge sockets start as
        # "probing" and only become dispatchable after a successful upstream
        # probe.  This prevents stale/invalid-key bridges from entering the
        # request pool merely because the websocket connected.
        self.client_health: Dict[int, Dict[str, Any]] = {}
        # uid -> platform lifecycle information learned by manager status
        # polling.  Gateway dispatch uses expire_at/remain_sec to drain nodes
        # before the one-hour lease expires.
        self.uid_lifecycle: Dict[str, Dict[str, Any]] = {}
        # uid -> unix timestamp until which the UID is banned from dispatch
        # because an upstream invalid_key was observed.
        self.bad_key_uids: Dict[str, float] = {}
        self.bad_key_reasons: Dict[str, str] = {}
        # uid -> minimum bridge epoch accepted while bad_key ban is active.
        # This lets freshly injected bridge.py copies reconnect after a hard
        # rebuild, while old stale copies keep getting rejected.
        self.uid_min_bridge_epoch: Dict[str, int] = {}
        # Targeted hard rebuild requests consumed by AccountManager instances.
        self.uid_rebuild_requests: Dict[str, Dict[str, Any]] = {}
        # Monotonic global rebuild generation.  Each AccountManager remembers
        # the generation it has consumed, so /api/rebuild can be hard-rebuild
        # semantics without relying on a single shared Event flag remaining set.
        self.global_rebuild_generation: int = 0
        self.metrics_started_at: float = time.time()
        self.metrics_history_last_snapshot: Dict[str, Any] | None = None
        self.metrics: Dict[str, Any] = self._default_metrics()
        self.recent_errors: deque = deque(maxlen=500)
        self.active_requests: Dict[str, Dict[str, Any]] = {}
        self.recent_dispatches: deque = deque(maxlen=100)
        self.recent_agent_runs: deque = deque(maxlen=200)
        self.recent_openclaw_events: deque = deque(maxlen=1000)
        # uid -> sanitized hello-ok protocol feature summary from OpenClaw.
        self.openclaw_features_by_uid: Dict[str, Any] = {}
        self.manager_status: Dict[str, Any] = {}

    def start_dispatch(self, record: Dict[str, Any]) -> None:
        node_request_id = str(record.get("node_request_id") or record.get("req_id") or "")
        if not node_request_id:
            return
        now = time.time()
        clean_record = dict(record)
        clean_record.setdefault("started_at", now)
        clean_record.setdefault("first_byte_at", None)
        clean_record.setdefault("finished_at", None)
        clean_record.setdefault("status_code", None)
        clean_record.setdefault("end_reason", "")
        self.active_requests[node_request_id] = clean_record

    def mark_dispatch_first_byte(self, node_request_id: str, status_code: int | None = None) -> None:
        record = self.active_requests.get(node_request_id)
        if not record:
            return
        record["first_byte_at"] = record.get("first_byte_at") or time.time()
        if status_code is not None:
            record["status_code"] = int(status_code)

    def finish_dispatch(
        self,
        node_request_id: str,
        *,
        status_code: int | None = None,
        end_reason: str = "finished",
    ) -> Dict[str, Any] | None:
        record = self.active_requests.pop(node_request_id, None)
        if not record:
            return None
        finished_at = time.time()
        record["finished_at"] = finished_at
        if status_code is not None:
            record["status_code"] = int(status_code)
        record["end_reason"] = str(end_reason or "finished")[:120]
        started_at = float(record.get("started_at") or finished_at)
        record["elapsed_ms"] = round(max(0.0, finished_at - started_at) * 1000, 2)
        self.recent_dispatches.append(record)
        return record

    def build_requests_snapshot(self) -> Dict[str, Any]:
        now = time.time()

        def with_elapsed(record: Dict[str, Any]) -> Dict[str, Any]:
            item = dict(record)
            started_at = float(item.get("started_at") or now)
            finished_at = item.get("finished_at")
            end_at = float(finished_at) if finished_at else now
            item["elapsed_ms"] = round(max(0.0, end_at - started_at) * 1000, 2)
            return item

        active = [with_elapsed(item) for item in self.active_requests.values()]
        active.sort(key=lambda item: float(item.get("started_at") or 0))
        recent = [with_elapsed(item) for item in reversed(self.recent_dispatches)]
        return {
            "generated_at": int(now),
            "active_requests": active,
            "recent_dispatches": recent,
        }

    @staticmethod
    def _default_metrics() -> Dict[str, Any]:
        return {
            "requests_total": 0,
            "requests_succeeded": 0,
            "requests_failed": 0,
            "streaming_requests": 0,
            "non_streaming_requests": 0,
            "attempts_total": 0,
            "attempts_succeeded": 0,
            "attempts_failed": 0,
            "request_latency_sum_ms": 0.0,
            "request_first_byte_latency_sum_ms": 0.0,
            "request_latency_samples_ms": deque(maxlen=2048),
            "request_first_byte_samples_ms": deque(maxlen=2048),
            "status_codes": {},
            "routes": {},
            "nodes": {},
            "tokens": {
                "requests_with_usage": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

state = GatewayState()
