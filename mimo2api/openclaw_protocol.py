"""OpenClaw protocol catalog and low-risk schema helpers.

This module intentionally contains only protocol metadata and small summarizers.
It does not open network connections and does not carry credentials.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

OPENCLAW_PROTOCOL_VERSION = 3
DEFAULT_AGENT_ID = "main"
DEFAULT_SESSION_KEY = "agent:main:main"

CONNECT_PARAMS_TEMPLATE: dict[str, Any] = {
    "minProtocol": OPENCLAW_PROTOCOL_VERSION,
    "maxProtocol": OPENCLAW_PROTOCOL_VERSION,
    "client": {
        "id": "cli",
        "version": "mimo-claw-ui",
        "platform": "Linux x86_64",
        "mode": "cli",
    },
    "role": "operator",
    "scopes": [
        "operator.admin",
        "operator.read",
        "operator.write",
        "operator.approvals",
        "operator.pairing",
    ],
    "caps": ["tool-events"],
    "userAgent": "Mozilla/5.0",
    "locale": "zh-CN",
}

KNOWN_METHODS: tuple[str, ...] = (
    "health",
    "doctor.memory.status",
    "logs.tail",
    "channels.status",
    "channels.logout",
    "status",
    "usage.status",
    "usage.cost",
    "tts.status",
    "tts.providers",
    "tts.enable",
    "tts.disable",
    "tts.convert",
    "tts.setProvider",
    "config.get",
    "config.set",
    "config.apply",
    "config.patch",
    "config.schema",
    "config.schema.lookup",
    "exec.approvals.get",
    "exec.approvals.set",
    "exec.approvals.node.get",
    "exec.approvals.node.set",
    "exec.approval.request",
    "exec.approval.waitDecision",
    "exec.approval.resolve",
    "wizard.start",
    "wizard.next",
    "wizard.cancel",
    "wizard.status",
    "talk.config",
    "talk.mode",
    "models.list",
    "tools.catalog",
    "agents.list",
    "agents.create",
    "agents.update",
    "agents.delete",
    "agents.files.list",
    "agents.files.get",
    "agents.files.set",
    "skills.status",
    "skills.bins",
    "skills.install",
    "skills.update",
    "update.run",
    "voicewake.get",
    "voicewake.set",
    "secrets.reload",
    "secrets.resolve",
    "sessions.list",
    "sessions.preview",
    "sessions.patch",
    "sessions.reset",
    "sessions.delete",
    "sessions.compact",
    "last-heartbeat",
    "set-heartbeats",
    "wake",
    "node.pair.request",
    "node.pair.list",
    "node.pair.approve",
    "node.pair.reject",
    "node.pair.verify",
    "device.pair.list",
    "device.pair.approve",
    "device.pair.reject",
    "device.pair.remove",
    "device.token.rotate",
    "device.token.revoke",
    "node.rename",
    "node.list",
    "node.describe",
    "node.pending.drain",
    "node.pending.enqueue",
    "node.invoke",
    "node.pending.pull",
    "node.pending.ack",
    "node.invoke.result",
    "node.event",
    "node.canvas.capability.refresh",
    "cron.list",
    "cron.status",
    "cron.add",
    "cron.update",
    "cron.remove",
    "cron.run",
    "cron.runs",
    "gateway.identity.get",
    "system-presence",
    "system-event",
    "send",
    "agent",
    "agent.identity.get",
    "agent.wait",
    "browser.request",
    "chat.history",
    "chat.abort",
    "chat.send",
)

KNOWN_EVENTS: tuple[str, ...] = (
    "connect.challenge",
    "agent",
    "chat",
    "presence",
    "tick",
    "talk.mode",
    "shutdown",
    "health",
    "heartbeat",
    "cron",
    "node.pair.requested",
    "node.pair.resolved",
    "node.invoke.request",
    "device.pair.requested",
    "device.pair.resolved",
    "voicewake.changed",
    "exec.approval.requested",
    "exec.approval.resolved",
    "update.available",
)

METHOD_CATEGORIES: dict[str, str] = {
    "health": "health",
    "status": "health",
    "doctor.memory.status": "health",
    "logs.tail": "diagnostics",
    "channels.status": "channels",
    "channels.logout": "channels",
    "usage.status": "usage",
    "usage.cost": "usage",
    "tts.status": "tts",
    "tts.providers": "tts",
    "tts.enable": "tts",
    "tts.disable": "tts",
    "tts.convert": "tts",
    "tts.setProvider": "tts",
    "config.get": "config",
    "config.set": "config",
    "config.apply": "config",
    "config.patch": "config",
    "config.schema": "config",
    "config.schema.lookup": "config",
    "exec.approvals.get": "approval",
    "exec.approvals.set": "approval",
    "exec.approvals.node.get": "approval",
    "exec.approvals.node.set": "approval",
    "exec.approval.request": "approval",
    "exec.approval.waitDecision": "approval",
    "exec.approval.resolve": "approval",
    "wizard.start": "wizard",
    "wizard.next": "wizard",
    "wizard.cancel": "wizard",
    "wizard.status": "wizard",
    "talk.config": "talk",
    "talk.mode": "talk",
    "models.list": "models_tools",
    "tools.catalog": "models_tools",
    "agents.list": "agents",
    "agents.create": "agents",
    "agents.update": "agents",
    "agents.delete": "agents",
    "agents.files.list": "agent_files",
    "agents.files.get": "agent_files",
    "agents.files.set": "agent_files",
    "skills.status": "skills",
    "skills.bins": "skills",
    "skills.install": "skills",
    "skills.update": "skills",
    "update.run": "update",
    "voicewake.get": "voicewake",
    "voicewake.set": "voicewake",
    "secrets.reload": "secrets",
    "secrets.resolve": "secrets",
    "sessions.list": "sessions",
    "sessions.preview": "sessions",
    "sessions.patch": "sessions",
    "sessions.reset": "sessions",
    "sessions.delete": "sessions",
    "sessions.compact": "sessions",
    "last-heartbeat": "heartbeat",
    "set-heartbeats": "heartbeat",
    "wake": "runtime",
    "node.pair.request": "node_pairing",
    "node.pair.list": "node_pairing",
    "node.pair.approve": "node_pairing",
    "node.pair.reject": "node_pairing",
    "node.pair.verify": "node_pairing",
    "device.pair.list": "device_pairing",
    "device.pair.approve": "device_pairing",
    "device.pair.reject": "device_pairing",
    "device.pair.remove": "device_pairing",
    "device.token.rotate": "device_pairing",
    "device.token.revoke": "device_pairing",
    "node.rename": "node",
    "node.list": "node",
    "node.describe": "node",
    "node.pending.drain": "node_pending",
    "node.pending.enqueue": "node_pending",
    "node.invoke": "node_invoke",
    "node.pending.pull": "node_pending",
    "node.pending.ack": "node_pending",
    "node.invoke.result": "node_invoke",
    "node.event": "node_invoke",
    "node.canvas.capability.refresh": "node_canvas",
    "cron.list": "cron",
    "cron.status": "cron",
    "cron.add": "cron",
    "cron.update": "cron",
    "cron.remove": "cron",
    "cron.run": "cron",
    "cron.runs": "cron",
    "gateway.identity.get": "identity",
    "system-presence": "system",
    "system-event": "system",
    "send": "message",
    "agent": "agent_run",
    "agent.identity.get": "identity",
    "agent.wait": "agent_run",
    "browser.request": "browser",
    "chat.history": "chat",
    "chat.abort": "chat",
    "chat.send": "chat",
}

# Methods verified as low-impact/read-only in the 2026-06-01 matrix.
READ_ONLY_VERIFIED: frozenset[str] = frozenset(
    {
        "health",
        "doctor.memory.status",
        "status",
        "usage.status",
        "usage.cost",
        "tts.status",
        "tts.providers",
        "config.get",
        "config.schema",
        "config.schema.lookup",
        "exec.approvals.get",
        "models.list",
        "tools.catalog",
        "agents.list",
        "agents.files.list",
        "agents.files.get",
        "skills.status",
        "voicewake.get",
        "sessions.list",
        "last-heartbeat",
        "node.pair.list",
        "device.pair.list",
        "node.list",
        "cron.list",
        "cron.status",
        "cron.runs",
        "gateway.identity.get",
        "agent.identity.get",
        "system-presence",
        "chat.history",
    }
)

CURRENTLY_IMPLEMENTED: frozenset[str] = frozenset(
    {"connect", "sessions.list", "chat.history", "chat.send", "agent", "agent.wait"}
)

MUTATING_METHOD_PREFIXES: tuple[str, ...] = (
    "channels.logout",
    "tts.enable",
    "tts.disable",
    "tts.convert",
    "tts.setProvider",
    "config.set",
    "config.apply",
    "config.patch",
    "exec.approvals.set",
    "exec.approvals.node.set",
    "exec.approval.request",
    "exec.approval.resolve",
    "wizard.start",
    "wizard.next",
    "wizard.cancel",
    "agents.create",
    "agents.update",
    "agents.delete",
    "agents.files.set",
    "skills.install",
    "skills.update",
    "update.run",
    "voicewake.set",
    "secrets.reload",
    "sessions.patch",
    "sessions.reset",
    "sessions.delete",
    "sessions.compact",
    "set-heartbeats",
    "wake",
    "node.pair.request",
    "node.pair.approve",
    "node.pair.reject",
    "node.pair.verify",
    "device.pair.approve",
    "device.pair.reject",
    "device.pair.remove",
    "device.token.rotate",
    "device.token.revoke",
    "node.rename",
    "node.pending.drain",
    "node.pending.enqueue",
    "node.invoke",
    "node.pending.pull",
    "node.pending.ack",
    "node.invoke.result",
    "node.event",
    "node.canvas.capability.refresh",
    "cron.add",
    "cron.update",
    "cron.remove",
    "cron.run",
    "system-event",
    "send",
    "agent",
    "browser.request",
    "chat.abort",
    "chat.send",
)

PARAMETER_HINTS: dict[str, dict[str, Any]] = {
    "connect": {
        "required": ["minProtocol", "maxProtocol", "client", "role", "scopes", "caps", "userAgent", "locale"],
        "meaning": "WebSocket 首包协商；决定协议版本、operator 角色、scope 权限和 tool-events 能力。",
        "known_good": CONNECT_PARAMS_TEMPLATE,
    },
    "sessions.list": {
        "required": [],
        "optional": ["includeGlobal", "includeUnknown", "limit"],
        "meaning": "列出 agent 会话；mimo2api 用第一项 key 作为后续 chat.history 的真实 sessionKey。",
        "known_good": {"includeGlobal": True, "includeUnknown": False, "limit": 120},
    },
    "chat.history": {
        "required": ["sessionKey"],
        "optional": ["limit", "before", "after"],
        "meaning": "加载 UI/chat 历史上下文；初始化阶段用于让后续会话与官方 Web UI 对齐。",
        "known_good": {"sessionKey": DEFAULT_SESSION_KEY, "limit": 200},
    },
    "chat.send": {
        "required": ["sessionKey", "message", "idempotencyKey"],
        "optional": ["deliver"],
        "meaning": "普通 UI chat 通道；可产生 events.chat，但不是工具执行入口。",
        "known_good": {"sessionKey": DEFAULT_SESSION_KEY, "message": "...", "deliver": False, "idempotencyKey": "uuid"},
    },
    "agent": {
        "required": ["agentId", "message", "idempotencyKey"],
        "meaning": "真正的 agent/tool 执行入口；返回/关联 runId，执行证据在 events.agent。",
        "known_good": {"agentId": DEFAULT_AGENT_ID, "message": "...", "idempotencyKey": "uuid-run-id"},
    },
    "agent.wait": {
        "required": ["runId"],
        "meaning": "等待 agent run 进入完成态；只能证明 run 结束，不能单独证明 bridge 已连接。",
        "known_good": {"runId": "uuid-run-id"},
    },
    "sessions.preview": {
        "required": ["keys"],
        "meaning": "批量预览会话；矩阵验证显示参数名是 keys，不是 sessionKey。",
        "known_good": {"keys": [DEFAULT_SESSION_KEY]},
    },
    "agent.identity.get": {
        "required": [],
        "meaning": "读取当前默认 agent 身份信息；实测返回 agentId/name/avatar。",
        "known_good": {},
    },
    "gateway.identity.get": {
        "required": [],
        "meaning": "读取 gateway 设备身份；返回 deviceId/publicKey 等公钥身份字段。",
        "known_good": {},
    },
    "config.schema.lookup": {
        "required": ["path"],
        "meaning": "按路径查询配置 schema 子树；实测 path=browser 返回 browser 配置 schema/hint/children。",
        "known_good": {"path": "browser"},
    },
    "doctor.memory.status": {
        "required": [],
        "meaning": "读取 agent memory/embedding 后端状态；只读诊断。",
        "known_good": {},
    },
    "models.list": {
        "required": [],
        "meaning": "读取可用模型列表；只读。",
        "known_good": {},
    },
    "tools.catalog": {
        "required": [],
        "meaning": "读取 agent 可用工具目录；实测返回 profiles/groups/tools。",
        "known_good": {},
    },
    "agents.files.list": {
        "required": ["agentId"],
        "meaning": "列出指定 agent workspace 支持的固定文件；实测返回 workspace 与 files[]。",
        "known_good": {"agentId": DEFAULT_AGENT_ID},
    },
    "agents.files.get": {
        "required": ["agentId", "name"],
        "meaning": "读取指定 agent workspace 固定文件内容；参数是 name，不是 path；实测支持 AGENTS.md/SOUL.md/TOOLS.md/IDENTITY.md/USER.md/HEARTBEAT.md 等；AGENTS.md/SOUL.md 当前内容可能是提示词恢复版，不应当作真实最新文件。",
        "known_good": {"agentId": DEFAULT_AGENT_ID, "name": "AGENTS.md"},
    },
    "agents.files.set": {
        "required": ["agentId", "name", "content"],
        "meaning": "写入指定 agent workspace 固定文件；只允许受支持的 name，任意临时文件名会返回 unsupported file；HEARTBEAT.md 已验证可备份、写入、读取并恢复；AGENTS.md/SOUL.md 未写入，需用户提供真实最新内容后才能安全验证。",
        "known_good": {"agentId": DEFAULT_AGENT_ID, "name": "HEARTBEAT.md", "content": "..."},
    },
    "exec.approvals.get": {
        "required": [],
        "meaning": "读取全局 exec approval 配置；实测返回 path/exists/hash/file，file 含 version/socket/defaults/agents。",
        "known_good": {},
    },
    "exec.approvals.node.get": {
        "required": ["nodeId"],
        "meaning": "读取指定 node 的 exec approval 配置；缺 nodeId 会 schema 错，未知 node 返回 NOT_CONNECTED。",
        "known_good": {"nodeId": "node-id"},
    },
    "exec.approval.waitDecision": {
        "required": ["id"],
        "meaning": "等待指定 approval 决策；参数名是 id，不是 approvalId；未知/过期 id 返回 not found。",
        "known_good": {"id": "approval-id"},
    },
    "exec.approval.request": {
        "required": ["command"],
        "optional": ["cwd", "nodeId", "host", "security", "ask", "agentId", "resolvedPath", "sessionKey"],
        "meaning": "发起审批请求；实测 request payload 会嵌套 command/cwd/nodeId/host/security/ask/agentId 等字段，返回 awaitable approval id。",
        "known_good": {"command": "echo probe", "cwd": "/tmp"},
    },
    "exec.approval.resolve": {
        "required": ["id", "decision"],
        "meaning": "解析审批请求；实测有效枚举包括 deny 与 allow-once；allow/approve/approved/accept/yes/approveOnce/allowOnce/permit/grant/ok/trusted 等其它候选均 invalid。",
        "known_good": {"id": "approval-id", "decision": "deny"},
        "observed_decisions": ["deny", "allow-once"],
    },
    "browser.request": {
        "required": ["method", "path"],
        "optional": ["body"],
        "meaning": "向 OpenClaw browser 管理端发请求；GET / 返回 runtime 状态；POST /start 与 POST /stop 可启停 browser；GET /profiles 返回 profile 状态；GET /tabs 返回 tab/target/wsUrl；POST /navigate 需要 body.url；GET /snapshot 返回 AI snapshot/refs；CDP 入口通过 tabs.wsUrl 暴露，本 RPC 未直接代理常见 CDP HTTP 路径。",
        "known_good": {"method": "GET", "path": "/"},
        "observed_paths": ["/", "/start", "/stop", "/profiles", "/tabs", "/navigate", "/snapshot"],
        "body_schemas": {"/navigate": {"body": {"url": "about:blank"}}},
        "rejected_inputs": {"POST /navigate body.url=data:...": "Navigation blocked: unsupported protocol \"data:\""},
        "not_found_paths": [
            "/html",
            "/content",
            "/dom",
            "/tree",
            "/accessibility",
            "/screenshot",
            "/reload",
            "/back",
            "/forward",
            "/evaluate",
            "/click",
            "/type",
            "/press",
            "/new",
            "/close",
            "/json/version",
            "/json/list",
            "/devtools/browser",
        ],
    },
    "config.patch": {
        "required": ["raw", "baseHash"],
        "meaning": "基于 config.get 返回的 raw/baseHash 写入配置；即使同内容也会更新 meta.lastTouchedAt，恢复需用备份 raw + 当前 baseHash。",
        "known_good": {"raw": "...", "baseHash": "hash-from-config.get"},
    },
    "config.apply": {
        "required": ["raw", "baseHash"],
        "meaning": "应用 raw 配置并可能触发 restart/sentinel；也要求 baseHash。",
        "known_good": {"raw": "...", "baseHash": "hash-from-config.get"},
    },
    "config.set": {
        "required": ["raw", "baseHash"],
        "meaning": "设置完整 raw 配置；schema 要求 raw，实际并发保护也要求 baseHash。",
        "known_good": {"raw": "...", "baseHash": "hash-from-config.get"},
    },
    "cron.add": {
        "required": ["name", "schedule", "sessionTarget", "payload"],
        "optional": ["delivery", "wakeMode", "enabled"],
        "meaning": "创建 cron job；schedule 支持 {kind:cron,expr} 或 {kind:every,everyMs}，payload 是对象，服务端会补 payload.kind=agentTurn；delivery.mode=none + sessionTarget=isolated 已验证可完成 agent turn 且 finished.status=ok。",
        "known_good": {
            "name": "probe",
            "schedule": {"kind": "cron", "expr": "0 0 1 1 *"},
            "sessionTarget": "isolated",
            "payload": {"message": "probe"},
            "delivery": {"mode": "none"},
        },
    },
    "cron.update": {
        "required": ["jobId", "patch"],
        "meaning": "更新 cron job；未知 jobId 返回 unknown cron job id。",
        "known_good": {"jobId": "cron-job-id", "patch": {"enabled": False}},
    },
    "cron.remove": {
        "required": ["jobId"],
        "meaning": "删除 cron job；不存在时返回 ok=true/removed=false。",
        "known_good": {"jobId": "cron-job-id"},
    },
    "cron.run": {
        "required": ["id"],
        "meaning": "手动运行 cron job；未知 id 返回 unknown cron job id；成功路径返回 enqueued/runId，随后 cron event action=started/finished。",
        "known_good": {"id": "cron-job-id"},
        "observed_finished": {
            "delivery_none": {"status": "ok", "delivered": False, "deliveryStatus": "not-delivered"},
            "delivery_announce_without_channel": {"status": "error", "deliveryStatus": "unknown", "summary_present": True},
        },
    },
    "sessions.compact": {
        "required": ["key"],
        "meaning": "压缩指定 session；参数名是 key，不接受 sessionKey/keys/dryRun；不存在 key 返回 compacted=false/reason=no sessionId。",
        "known_good": {"key": DEFAULT_SESSION_KEY},
    },
    "node.pair.request": {
        "required": ["nodeId"],
        "optional": ["isRepair"],
        "meaning": "为 node 创建配对请求；实测返回 status=pending/request.requestId/nodeId/isRepair/ts/created。",
        "known_good": {"nodeId": "node-id"},
    },
    "node.pair.approve": {
        "required": ["requestId"],
        "meaning": "批准 pending node 配对请求；返回 node.nodeId/token/createdAtMs/approvedAtMs。token 需要脱敏保存。",
        "known_good": {"requestId": "pair-request-id"},
    },
    "node.pair.reject": {
        "required": ["requestId"],
        "meaning": "拒绝 pending node 配对请求；只处理 pending request，不会删除已批准 paired node，批准后再 reject 返回 unknown requestId。",
        "known_good": {"requestId": "pair-request-id"},
    },
    "node.pair.verify": {
        "required": ["nodeId", "token"],
        "meaning": "验证已配对 node token；真实 schema 是 nodeId + token，不是 requestId。",
        "known_good": {"nodeId": "node-id", "token": "..."},
    },
    "node.pending.enqueue": {
        "required": ["nodeId", "type"],
        "meaning": "向 node pending 队列入队；operator 角色可见 schema，但完整 payload 仍需真实 node。",
        "known_good": {"nodeId": "node-id", "type": "..."},
    },
    "node.invoke": {
        "required": ["nodeId", "command", "idempotencyKey"],
        "meaning": "调用已连接 node 执行命令；参数是 nodeId/command/idempotencyKey，可带 params。真实闭环已用远端 sandbox 内官方 openclaw node run 验证：device.pair 授权 node role 后，node.list 出现 connected node，node.invoke(system.which/system.run.prepare/system.run/browser.proxy) 均可触发 node.invoke.request/result 并返回结构化结果。",
        "known_good": {"nodeId": "node-id", "command": "system.which", "params": {"bins": ["sh"]}, "idempotencyKey": "uuid"},
        "verified_closed_loop": {
            "uid": "6875021188",
            "node_id_source": "device.id from paired device identity",
            "commands": {
                "system.which": {
                    "params": {"bins": ["sh"]},
                    "result_shape": {"ok": True, "payload": {"bins": {"sh": "/usr/bin/sh"}}, "payloadJSON": "json string"},
                },
                "system.run.prepare": {
                    "params": {"command": ["/usr/bin/printf", "mimo2api-system-run-ok\\n"], "timeoutMs": 5000, "sessionKey": DEFAULT_SESSION_KEY},
                    "result_shape": {"ok": True, "payload": {"plan": {"argv": "command argv array", "commandText": "formatted command", "sessionKey": DEFAULT_SESSION_KEY}}},
                },
                "system.run": {
                    "approval_flow": "direct run returns SYSTEM_RUN_DENIED until exec.approval.request(twoPhase) is resolved with allow-once; approved invoke returns exitCode/stdout/stderr.",
                    "params": {"command": ["/usr/bin/printf", "mimo2api-system-run-ok\\n"], "rawCommand": "/usr/bin/printf mimo2api-system-run-ok\\n", "runId": "approval id", "approved": True, "approvalDecision": "allow-once"},
                    "result_shape": {"ok": True, "payload": {"exitCode": 0, "success": True, "stdout": "mimo2api-system-run-ok\n", "stderr": "", "error": None}},
                },
                "browser.proxy": {
                    "params": {"method": "GET", "path": "/profiles", "timeoutMs": 3000},
                    "result_shape": {"ok": True, "payload": {"result": {"profiles": "browser profile list with name/cdpPort/cdpUrl/running/tabCount/isDefault"}}},
                },
            },
        },
        "official_node_client": {
            "command": "openclaw node run",
            "client_id": "node-host",
            "client_mode": "node",
            "role": "node",
            "scopes": [],
            "caps": ["system"],
            "commands": ["browser.proxy", "system.run.prepare", "system.run", "system.which"],
            "protocol": 3,
        },
        "state_note": "本次闭环选择 uid=6875021188；为保留可复现能力，该 uid 的 paired device 已追加 node role/token，后台 node 进程已停止。",
    },
    "node.pending.pull": {
        "required": [],
        "meaning": "node 侧拉取 pending 队列；operator 角色 unauthorized，需 node role。",
    },
    "node.pending.ack": {
        "required": [],
        "meaning": "node 侧 ack pending 项；operator 角色 unauthorized，需 node role。",
    },
    "node.invoke.result": {
        "required": [],
        "meaning": "node 侧提交 invoke 结果；operator 角色 unauthorized，需 node role。",
    },
    "node.event": {
        "required": [],
        "meaning": "node 侧上报事件；operator 角色 unauthorized，需 node role。",
    },
    "node.describe": {
        "required": ["nodeId"],
        "meaning": "查看指定 node 详情；矩阵验证显示必须提供 nodeId。",
    },
    "chat.abort": {
        "required": ["sessionKey"],
        "optional": ["runId"],
        "meaning": "中止 session 中活跃 chat/run 的候选入口；无活跃 run 时返回 ok=true、aborted=false、runIds=[]。",
        "known_good": {"sessionKey": DEFAULT_SESSION_KEY, "runId": "optional-run-id"},
    },
}

EVENT_HINTS: dict[str, dict[str, Any]] = {
    "connect.challenge": {
        "meaning": "服务端要求客户端发送 connect RPC；必须先完成该挑战才会 hello-ok。",
        "payload_fields": {"challenge": "握手挑战/nonce（当前实现不需要显式回签）。"},
    },
    "agent": {
        "meaning": "agent run 实时事件，是判断工具链是否触发的主证据。",
        "payload_fields": {
            "runId": "agent run 唯一 ID；应与 agent.idempotencyKey / agent.wait.runId 对齐。",
            "stream": "子流类型：lifecycle、assistant、tool 等。",
            "data": "子流负载；结构随 stream 改变。",
            "sessionKey": "该 run 绑定的会话。",
            "seq": "run 内事件序号。",
            "ts": "服务端毫秒时间戳。",
        },
        "streams": {
            "lifecycle": "run 生命周期；data.phase=start/end，包含 startedAt/endedAt。",
            "assistant": "模型文本流；data.delta 为增量，data.text 为截至当前的累计文本。",
            "tool": "工具调用流；用于确认 exec/process/write 等工具链已触发；实测 data 包含 phase/name/toolCallId，start 阶段有 args，result 阶段有 isError/meta。",
        },
    },
    "chat": {
        "meaning": "UI/审计消息事件；可作为文本 fallback，但不能覆盖 events.agent 工具成功证据。",
        "payload_fields": {
            "runId": "相关 run ID（如存在）。",
            "sessionKey": "会话 key。",
            "seq": "事件序号。",
            "state": "delta/final 等 UI 消息状态。",
            "message": "role/content/timestamp 消息体。",
        },
    },
    "health": {"meaning": "服务端健康状态广播。"},
    "heartbeat": {"meaning": "心跳/保活事件。"},
    "presence": {"meaning": "gateway/node presence 变化。"},
    "tick": {"meaning": "服务端周期 tick。"},
    "cron": {
        "meaning": "cron 任务状态或运行事件。",
        "payload_fields": {
            "jobId": "cron job id。",
            "action": "started/finished 等动作。",
            "status": "finished 时的状态；实测可为 error。",
            "error": "delivery/channel 等失败原因；announce 未配置 channel 时出现。",
            "summary": "cron agent turn 摘要文本。",
            "delivered": "是否完成外部 delivery；delivery.mode=none 时为 false。",
            "deliveryStatus": "delivery 结果；实测 none 为 not-delivered，announce 缺 channel 为 unknown。",
            "sessionId": "cron run 创建的 session id。",
            "sessionKey": "cron run session key，形如 agent:main:cron:<job>:run:<session>。",
            "runAtMs": "运行时间毫秒。",
            "durationMs": "运行耗时毫秒。",
            "usage": "token usage；摘要中需脱敏。",
        },
    },
    "node.pair.requested": {"meaning": "node.pair.request 创建 pending 请求后的配对事件。"},
    "node.pair.resolved": {"meaning": "node 配对请求 approve/reject 后的解析事件。"},
    "node.invoke.request": {
        "meaning": "node 调用请求事件；对应 node.invoke/node.invoke.result 系列；已通过 uid=6875021188 的官方 node host 完成 system.which、system.run.prepare/system.run、browser.proxy 闭环。",
        "verified_by": "data/stateful_backups5/openclaw_6875021188_node_loop.json, data/stateful_backups5/openclaw_node_system_run_probe.json, data/stateful_backups5/openclaw_node_browser_proxy_probe.json",
    },
    "exec.approval.requested": {
        "meaning": "审批请求已创建。",
        "payload_fields": {
            "id": "approval id；用于 exec.approval.waitDecision/resolve。",
            "request": "原始审批请求对象。",
            "createdAtMs": "创建时间毫秒。",
            "expiresAtMs": "过期时间毫秒。",
        },
    },
    "exec.approval.resolved": {
        "meaning": "审批已解析。",
        "payload_fields": {
            "id": "approval id。",
            "decision": "最终决策，例如 deny。",
            "resolvedBy": "解析来源，实测为 cli。",
            "ts": "解析时间毫秒。",
            "request": "原始审批请求对象。",
        },
    },
}

ENVELOPE_FIELD_DICTIONARY: dict[str, str] = {
    "type": "WebSocket envelope 类型：req/res/event。",
    "id": "RPC 请求/响应关联 ID；req 与 res 必须一致。",
    "method": "RPC 方法名；以 hello-ok.features.methods 为准。",
    "params": "RPC 参数对象；不同 method schema 不同。",
    "ok": "响应是否成功；false 时读取 error。",
    "payload": "响应或事件主负载。",
    "error": "错误对象；通常含 message/code 等字段。",
    "event": "事件名；以 hello-ok.features.events 为准。",
    "seq": "连接级或事件级递增序号，用于排序和去重。",
    "stateVersion": "状态快照版本号；用于判断 snapshot/presence 是否更新。",
}


def build_connect_params() -> dict[str, Any]:
    """Return a fresh connect params dict so callers can mutate safely."""
    return {
        key: (value.copy() if isinstance(value, dict) else list(value) if isinstance(value, list) else value)
        for key, value in CONNECT_PARAMS_TEMPLATE.items()
    }


def is_mutating_method(method: str) -> bool:
    return method in MUTATING_METHOD_PREFIXES


def classify_method(method: str) -> dict[str, Any]:
    return {
        "method": method,
        "known": method == "connect" or method in KNOWN_METHODS,
        "category": "handshake" if method == "connect" else METHOD_CATEGORIES.get(method, "unknown"),
        "implemented_in_manager": method in CURRENTLY_IMPLEMENTED,
        "read_only_verified": method in READ_ONLY_VERIFIED,
        "mutating_or_execution": is_mutating_method(method),
        "parameter_hint": PARAMETER_HINTS.get(method, {}),
    }


def classify_event(event: str) -> dict[str, Any]:
    return {
        "event": event,
        "known": event in KNOWN_EVENTS,
        "category": event.split(".", 1)[0],
        "hint": EVENT_HINTS.get(event, {}),
    }


def _features_from_hello(payload: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    features = (payload or {}).get("features") if isinstance(payload, dict) else {}
    if not isinstance(features, dict):
        return list(KNOWN_METHODS), list(KNOWN_EVENTS)
    methods = features.get("methods") if isinstance(features.get("methods"), list) else list(KNOWN_METHODS)
    events = features.get("events") if isinstance(features.get("events"), list) else list(KNOWN_EVENTS)
    return [str(m) for m in methods], [str(e) for e in events]


def summarize_hello_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Persist only protocol/schema metadata from a hello-ok payload."""
    payload = payload if isinstance(payload, dict) else {}
    methods, events = _features_from_hello(payload)
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    session_defaults = payload.get("sessionDefaults") if isinstance(payload.get("sessionDefaults"), dict) else {}
    server = payload.get("server") if isinstance(payload.get("server"), dict) else {}
    return {
        "captured_at": int(time.time()),
        "type": payload.get("type"),
        "protocol": payload.get("protocol"),
        "server_version": server.get("version"),
        "server_conn_id_present": bool(server.get("connId")),
        "auth_mode": payload.get("authMode") or payload.get("auth", {}).get("mode") if isinstance(payload.get("auth"), dict) else payload.get("authMode"),
        "canvas_host_url_present": bool(payload.get("canvasHostUrl")),
        "default_agent_id": health.get("defaultAgentId") or payload.get("defaultAgentId"),
        "session_defaults": {
            "mainKey": session_defaults.get("mainKey"),
            "mainSessionKey": session_defaults.get("mainSessionKey"),
        },
        "method_count": len(methods),
        "event_count": len(events),
        "methods": methods,
        "events": events,
        "method_categories": dict(Counter(METHOD_CATEGORIES.get(m, "unknown") for m in methods)),
    }


def summarize_openclaw_event(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload") if isinstance(message, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    event_name = str(message.get("event", "")) if isinstance(message, dict) else ""
    result: dict[str, Any] = {
        "event": event_name,
        "known": event_name in KNOWN_EVENTS,
        "seq": message.get("seq") if isinstance(message, dict) else None,
    }
    if event_name == "agent":
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        result.update(
            {
                "run_id": payload.get("runId"),
                "session_key": payload.get("sessionKey"),
                "stream": payload.get("stream"),
                "run_seq": payload.get("seq"),
                "ts": payload.get("ts"),
                "phase": data.get("phase"),
                "is_error": data.get("isError"),
                "has_delta": bool(data.get("delta")),
                "text_len": len(str(data.get("text") or "")),
                "data_keys": sorted(data.keys()),
            }
        )
        if payload.get("stream") == "tool":
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            result.update(
                {
                    "tool_name": data.get("name"),
                    "tool_call_id_present": bool(data.get("toolCallId")),
                    "args_keys": sorted(args.keys()),
                    "meta_keys": sorted(meta.keys()),
                }
            )
    elif event_name == "chat":
        msg = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        result.update(
            {
                "run_id": payload.get("runId"),
                "session_key": payload.get("sessionKey"),
                "state": payload.get("state"),
                "role": msg.get("role"),
                "content_items": len(content),
            }
        )
    return result


def build_protocol_catalog(features: dict[str, Any] | None = None) -> dict[str, Any]:
    methods, events = _features_from_hello({"features": features} if features else None)
    categories: dict[str, list[str]] = {}
    for method in methods:
        categories.setdefault(METHOD_CATEGORIES.get(method, "unknown"), []).append(method)
    return {
        "protocol_version": OPENCLAW_PROTOCOL_VERSION,
        "method_count": len(methods),
        "event_count": len(events),
        "methods": {method: classify_method(method) for method in methods},
        "events": {event: classify_event(event) for event in events},
        "method_categories": categories,
        "implemented_methods": sorted(CURRENTLY_IMPLEMENTED),
        "read_only_verified_methods": sorted(READ_ONLY_VERIFIED),
        "envelope_fields": ENVELOPE_FIELD_DICTIONARY,
        "parameter_hints": PARAMETER_HINTS,
        "event_hints": EVENT_HINTS,
        "known_gaps": [
            "非阻塞后续补充：events.agent/tool 子流完整字段可在更多真实工具调用中继续采样。",
            "非阻塞后续补充：chat.abort 对活跃 agent run 的中止效果可在需要取消能力时再做低影响验证。",
            "非阻塞后续补充：browser.request 已确认管理路径与 snapshot；tabs.wsUrl/CDP 页面级动作仅在需要浏览器自动化能力时继续验证。",
            "非阻塞后续补充：node.invoke.* 已完成 system.which、system.run.prepare/system.run、browser.proxy 闭环；更复杂的 browser.proxy 页面动作按需验证。",
        ],
    }
