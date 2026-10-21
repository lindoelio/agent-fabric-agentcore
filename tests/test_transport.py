"""Opt-in real WebSocket transport tests, using a fixture agent and fake AWS keys."""

import asyncio
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_factory.app import FactoryApp
from agent_factory.bundles import BundleLoader, LocalObjects
from agent_factory.cli import ACPClient, websocket_headers
from agent_factory.process import DcodeProcess
from test_factory import REPO, release


@unittest.skipUnless(os.getenv("FACTORY_TEST_TRANSPORT") == "1", "Requires installed client dependencies")
class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_headers_complete_a_standard_websocket_handshake(self):
        import boto3
        import websockets
        from bedrock_agentcore.runtime import AgentCoreRuntimeClient
        from websockets.asyncio.server import serve

        sdk = AgentCoreRuntimeClient(region="us-west-2", session=boto3.Session(
            aws_access_key_id="testing", aws_secret_access_key="testing"))
        _, original = sdk.generate_ws_connection(
            "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/example-abcdefgh",
            session_id="test-session-0000000000000000000000")
        headers = websocket_headers(original)
        self.assertEqual(headers["Authorization"], original["Authorization"])
        self.assertEqual(headers["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"],
                         original["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"])

        async def check(ws):
            for name in ("Host", "Connection", "Upgrade", "Sec-WebSocket-Key"):
                self.assertEqual(len(ws.request.headers.get_all(name)), 1)
            self.assertEqual(ws.request.headers["Authorization"], original["Authorization"])
            await ws.send("connected")

        async with serve(check, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}", additional_headers=headers, proxy=None) as ws:
                self.assertEqual(await ws.recv(), "connected")

    async def test_client_approvals_and_resume_through_uvicorn(self):
        import uvicorn
        import websockets

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release(root / "objects")
            app = FactoryApp(
                loader=BundleLoader(LocalObjects(root / "objects")), state_root=root / "state",
                process_factory=lambda: DcodeProcess((sys.executable, str(REPO / "tests/fake_dcode.py"))))
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                async def started():
                    while not server.started:
                        if task.done():
                            await task
                            self.fail("Server stopped before startup")
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(started(), 10)
                for resume in (False, True):
                    # Wait for the prior connection to finish subprocess cleanup.
                    async def unlocked():
                        while app.lock.locked():
                            await asyncio.sleep(0.01)
                    await asyncio.wait_for(unlocked(), 10)
                    async with websockets.connect(f"ws://127.0.0.1:{port}/ws", proxy=None) as ws:
                        await ws.send(json.dumps({"type": "factory.open", "team": "software", "version": "v001"}))
                        client = ACPClient(ws)
                        self.assertEqual((await client.receive())["type"], "factory.ready")
                        await client.call("initialize", {"protocolVersion": 1})
                        result = await client.call("session/load" if resume else "session/new",
                                                   {"sessionId": "persisted-session", "cwd": "/etc"})
                        if resume:
                            self.assertTrue(result["restored"])
                        else:
                            self.assertEqual(result["sessionId"], "persisted-session")
                            with patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                                await asyncio.wait_for(client.call("session/prompt", {}), 10)
                            self.assertEqual(app.health()["status"], "Healthy")
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 15)
                sock.close()

    async def test_client_reassembles_large_unicode_messages(self):
        import websockets
        from websockets.asyncio.server import serve

        original = {"jsonrpc": "2.0", "id": 1, "result": {"text": "😀\n\\" * 50000}}

        async def emit(ws):
            async def send(event):
                await ws.send(event["text"])
            await FactoryApp.send_json(send, original)

        async with serve(emit, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}", max_size=64 * 1024, proxy=None) as ws:
                self.assertEqual(await ACPClient(ws).receive(), original)
