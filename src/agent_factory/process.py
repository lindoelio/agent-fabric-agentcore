"""dcode subprocess lifecycle and ACP message boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

from agent_factory.bundles import DefinitionError, Release

MAX_FRAME = 60 * 1024
MAX_ACP_MESSAGE = 8 * 1024 * 1024
METHODS = {
    "initialize", "session/new", "session/load", "session/list", "session/prompt",
    "session/cancel", "session/set_model", "session/set_mode", "session/set_config_option",
}


def client_message(raw: str, workspace: Path) -> dict:
    if len(raw.encode()) > MAX_FRAME:
        raise DefinitionError("Client frame exceeds 60 KiB; send a file reference instead.")
    try:
        message = json.loads(raw)
    except ValueError as exc:
        raise DefinitionError("Expected one ACP JSON-RPC object per frame.") from exc
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise DefinitionError("Expected an ACP JSON-RPC 2.0 object.")
    method = message.get("method")
    if method is None:
        if "id" not in message or ("result" not in message and "error" not in message):
            raise DefinitionError("Invalid ACP response.")
        return message  # Includes permission responses from the remote client.
    if method not in METHODS:
        raise DefinitionError("Unsupported ACP method.")
    params = message.setdefault("params", {})
    if not isinstance(params, dict):
        raise DefinitionError("ACP params must be an object.")
    if method in {"session/new", "session/load"}:
        params["cwd"] = str(workspace.resolve())
        params["mcpServers"] = []  # Tool definitions come from the published profile.
        params.pop("additionalDirectories", None)
    if method == "initialize":
        params["clientCapabilities"] = {
            "fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False
        }
    return message


class DcodeProcess:
    def __init__(self, command: tuple[str, ...] = ("dcode",)):
        self.command = command
        self.process: asyncio.subprocess.Process | None = None

    async def start(self, root: Path, release: Release) -> None:
        environment = os.environ.copy()
        environment.update({
            "DEEPAGENTS_HOME": str((root / "profile").resolve()),
            "DEEPAGENTS_CODE_READ_PROJECT_DOTENV": "false",
            "PYTHONUNBUFFERED": "1",
        })
        (root / "project").mkdir(parents=True, exist_ok=True)
        self.process = await asyncio.create_subprocess_exec(
            *self.command, "--agent", release.team, "--model", release.model, "--acp",
            cwd=root / "project", env=environment, start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=MAX_ACP_MESSAGE,
        )

    async def write(self, message: dict) -> None:
        assert self.process and self.process.stdin
        self.process.stdin.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
        await self.process.stdin.drain()

    async def read(self) -> dict | None:
        assert self.process and self.process.stdout
        line = await self.process.stdout.readline()
        if not line:
            return None
        message = json.loads(line)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise DefinitionError("dcode returned invalid ACP output.")
        return message

    async def drain_stderr(self) -> None:
        # Do not forward raw stderr: upstream tools may print credentials or file content.
        assert self.process and self.process.stderr
        while await self.process.stderr.read(8192):
            pass

    async def stop(self) -> None:
        if self.process is None:
            return
        # Include children (MCP servers, shell tools), even if dcode already exited.
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except TimeoutError:
            pass
        finally:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await self.process.wait()
