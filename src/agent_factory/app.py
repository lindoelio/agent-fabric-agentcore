"""Small ASGI service implementing AgentCore HTTP and WebSocket contracts."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

from agent_factory.bundles import BundleLoader, DefinitionError, LocalObjects, S3Objects
from agent_factory.process import DcodeProcess, MAX_FRAME, client_message

logger = logging.getLogger(__name__)


class FactoryApp:
    def __init__(self, loader=None, state_root=None, process_factory=DcodeProcess):
        self.loader = loader
        self.root = Path(state_root or os.getenv("FACTORY_STATE_ROOT", "/mnt/workspace"))
        self.process_factory = process_factory
        self.lock = asyncio.Lock()
        self.prompts: set[str | int] = set()
        self.busy = False
        self.updated = time.time()

    def health(self) -> dict:
        busy = bool(self.prompts)
        if busy != self.busy:
            self.busy, self.updated = busy, time.time()
        return {"status": "HealthyBusy" if busy else "Healthy", "time_of_last_update": self.updated}

    def get_loader(self):
        if self.loader is None:
            local = os.getenv("FACTORY_LOCAL_DEFINITIONS")
            objects = LocalObjects(Path(local)) if local else S3Objects(os.environ["DEFINITIONS_BUCKET"])
            self.loader = BundleLoader(objects, os.getenv("DEFINITIONS_PREFIX", "teams"))
        return self.loader

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "http":
            await self.http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self.websocket(scope, receive, send)

    async def http(self, scope, receive, send):
        status, result = 404, {"error": "not_found"}
        if scope["path"] == "/ping" and scope["method"] == "GET":
            status, result = 200, self.health()
        elif scope["path"] == "/invocations" and scope["method"] == "POST":
            body = bytearray()
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                body.extend(event.get("body", b""))
                if len(body) > MAX_FRAME:
                    status, result = 413, {"error": "payload_too_large"}
                    break
                if not event.get("more_body"):
                    try:
                        value = json.loads(body or b"{}")
                        if not isinstance(value, dict) or value.get("action", "describe") != "describe":
                            raise ValueError
                        status, result = 200, {
                            "service": "agent-factory", "transport": "ACP over WebSocket",
                            "path": "/ws", "open": {"type": "factory.open", "team": "software", "version": "v001"},
                        }
                    except ValueError:
                        status, result = 400, {"error": "Use action=describe; interactive execution uses /ws."}
                    break
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": json.dumps(result).encode()})

    async def websocket(self, scope, receive, send):
        if scope["path"] != "/ws" or self.lock.locked():
            await send({"type": "websocket.close", "code": 1008})
            return
        async with self.lock:
            process = self.process_factory()
            tasks = []
            try:
                if (await receive())["type"] != "websocket.connect":
                    return
                await send({"type": "websocket.accept"})
                event = await asyncio.wait_for(receive(), timeout=30)
                text = event.get("text", "")
                if len(text.encode()) > MAX_FRAME:
                    raise DefinitionError("Opening frame is too large.")
                opening = json.loads(text)
                if not isinstance(opening, dict) or set(opening) != {"type", "team", "version"}:
                    raise DefinitionError("First frame must be factory.open with team and version.")
                if opening["type"] != "factory.open":
                    raise DefinitionError("First frame must be factory.open.")
                loader = self.get_loader()
                release = await asyncio.to_thread(
                    loader.materialize, self.root, opening["team"], opening["version"]
                )
                await process.start(self.root, release)
                await self.send_json(send, {
                    "type": "factory.ready", "team": release.team, "version": release.version,
                    "manifest_sha256": release.manifest_digest,
                    "workspace": str((self.root / "project").resolve()),
                })
                tasks = [
                    asyncio.create_task(self.from_client(process, receive)),
                    asyncio.create_task(self.from_agent(process, send)),
                    asyncio.create_task(process.drain_stderr()),
                ]
                # stderr may end before stdout is drained; it is not a session lifetime signal.
                done, _ = await asyncio.wait(tasks[:2], return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            except (DefinitionError, ValueError, TimeoutError) as exc:
                detail = str(exc) if isinstance(exc, DefinitionError) else "Invalid message or handshake timeout."
                await self.send_json(send, {"type": "factory.error", "detail": detail})
            except Exception:
                # Do not include prompts, raw SDK errors, or credential-bearing URLs in logs.
                logger.error("agent_session_failed")
                await self.send_json(send, {"type": "factory.error", "detail": "Agent session failed. Check deployment and model access."})
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await process.stop()
                self.prompts.clear()
                self.health()
                try:
                    await send({"type": "websocket.close", "code": 1000})
                except (OSError, RuntimeError):
                    pass

    async def from_client(self, process, receive):
        while True:
            event = await receive()
            if event["type"] == "websocket.disconnect":
                return
            if "text" not in event:
                raise DefinitionError("Only JSON text frames are accepted.")
            message = client_message(event["text"], self.root / "project")
            if message.get("method") == "session/prompt":
                request_id = message.get("id")
                if type(request_id) not in (str, int):
                    raise DefinitionError("Prompt requires a string or integer request ID.")
                if self.prompts:
                    raise DefinitionError("Only one prompt may be active at a time.")
                self.prompts.add(request_id)
                self.health()
            await process.write(message)

    async def from_agent(self, process, send):
        while True:
            message = await process.read()
            if message is None:
                await self.send_json(send, {"type": "factory.error", "detail": "dcode exited; reconnect and load the saved ACP session."})
                return
            if "method" not in message:
                request_id = message.get("id")
                if type(request_id) in (str, int):
                    self.prompts.discard(request_id)
                    self.health()
            await self.send_json(send, message)

    @staticmethod
    async def send_json(send, message):
        raw = json.dumps(message, ensure_ascii=False)
        if len(raw.encode()) <= MAX_FRAME:
            await send({"type": "websocket.send", "text": raw})
            return
        # Cloud limit is per WebSocket frame. Large ACP notifications are reassembled by the client.
        transfer = uuid.uuid4().hex
        chunks = [raw[i:i + 8000] for i in range(0, len(raw), 8000)]
        for index, data in enumerate(chunks):
            envelope = {"type": "factory.chunk", "id": transfer, "index": index, "total": len(chunks), "data": data}
            await send({"type": "websocket.send", "text": json.dumps(envelope, ensure_ascii=False)})


app = FactoryApp()
