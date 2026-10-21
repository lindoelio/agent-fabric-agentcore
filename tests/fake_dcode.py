"""Protocol fixture: subprocess with ACP requests, permissions, updates and resume."""

import json
import os
import sys
from pathlib import Path

state = Path(os.environ["DEEPAGENTS_HOME"]) / ".state"
state.mkdir(exist_ok=True)
pending = None


def emit(value):
    print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)


for line in sys.stdin:
    value = json.loads(line)
    method = value.get("method")
    if method == "initialize":
        emit({"id": value["id"], "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}})
    elif method == "session/new":
        (state / "saved.json").write_text(json.dumps(value["params"]))
        emit({"id": value["id"], "result": {"sessionId": "persisted-session"}})
    elif method == "session/load":
        saved = json.loads((state / "saved.json").read_text())
        emit({"id": value["id"], "result": {"restored": True, "cwd": saved["cwd"]}})
    elif method == "session/prompt":
        pending = value["id"]
        emit({"id": "permission-1", "method": "session/request_permission", "params": {
            "sessionId": "persisted-session", "toolCall": {"title": "Edit file"},
            "options": [{"optionId": "deny", "name": "Deny", "kind": "reject_once"}]
        }})
    elif value.get("id") == "permission-1":
        emit({"method": "session/update", "params": {"sessionId": "persisted-session", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "verified"}
        }}})
        emit({"id": pending, "result": {"stopReason": "end_turn"}})
