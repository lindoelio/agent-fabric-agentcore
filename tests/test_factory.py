from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from agent_factory.app import FactoryApp
from agent_factory.bundles import BundleLoader, DefinitionError, LocalObjects, digest, pack, safe_path
from agent_factory.cli import publish
from agent_factory.process import DcodeProcess, MAX_FRAME, client_message

REPO = Path(__file__).resolve().parents[1]


def release(root, version="v001"):
    raw, files = pack(REPO / "examples/software", "software", version, "bedrock_converse:example")
    destination = root / "teams/software/releases" / version
    for name, content in {**files, "manifest.json": raw}.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return destination


class BundleTests(unittest.TestCase):
    def test_rejects_traversal_secrets_and_noncanonical_paths(self):
        for path in ["../etc/passwd", "/etc/passwd", "profile/../x", "profile//a", "profile/./a",
                     "profile\\x", "profile/.env", "profile/a/.state/sessions.db", "profile/a/memories/x"]:
            with self.subTest(path=path), self.assertRaises(DefinitionError):
                safe_path(path)

    def test_rejects_corrupted_release_without_installing_partial_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = release(root / "objects")
            (source / "profile/software/AGENTS.md").write_text("tampered")
            loader = BundleLoader(LocalObjects(root / "objects"))
            with self.assertRaisesRegex(DefinitionError, "checksum"):
                loader.materialize(root / "state", "software", "v001")
            self.assertFalse((root / "state/profile").exists())
            self.assertFalse((root / "state/binding.json").exists())

    def test_reconnect_preserves_state_and_rejects_changed_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = release(root / "objects")
            loader = BundleLoader(LocalObjects(root / "objects"))
            loader.materialize(root / "state", "software", "v001")
            memory = root / "state/profile/software/memories/learned.md"
            memory.parent.mkdir()
            memory.write_text("Keep this")
            loader.materialize(root / "state", "software", "v001")
            self.assertEqual(memory.read_text(), "Keep this")
            release(root / "objects", "v002")
            with self.assertRaisesRegex(DefinitionError, "pinned"):
                loader.materialize(root / "state", "software", "v002")
            manifest = json.loads((source / "manifest.json").read_text())
            manifest["model"] = "bedrock_converse:changed"
            (source / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(DefinitionError, "pinned"):
                loader.materialize(root / "state", "software", "v001")

    def test_pack_is_deterministic_and_rejects_symlinks(self):
        args = (REPO / "examples/software", "software", "v001", "bedrock_converse:test")
        self.assertEqual(pack(*args), pack(*args))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "profile").mkdir()
            (root / "profile/escape").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(DefinitionError, "symlinks"):
                pack(root, "software", "v001", "bedrock_converse:test")


class PublisherTests(unittest.TestCase):
    def test_conditional_upload_is_idempotent_and_manifest_is_last(self):
        class Exists(Exception):
            response = {"ResponseMetadata": {"HTTPStatusCode": 412}}

        class S3:
            def __init__(self):
                self.values = {}

            def put_object(self, Bucket, Key, Body, IfNoneMatch):
                assert IfNoneMatch == "*"
                if Key in self.values:
                    raise Exists()
                self.values[Key] = Body

            def get_object(self, Bucket, Key):
                return {"Body": io.BytesIO(self.values[Key])}

        s3 = S3()
        files = {"profile/software/AGENTS.md": b"instructions", "manifest.json": b"manifest"}
        publish(s3, "bucket", "teams/software/releases/v001", files)
        publish(s3, "bucket", "teams/software/releases/v001", files)
        self.assertTrue(list(s3.values)[-1].endswith("manifest.json"))
        with self.assertRaisesRegex(DefinitionError, "already exists"):
            publish(s3, "bucket", "teams/software/releases/v001", {"manifest.json": b"different"})


class ProtocolTests(unittest.TestCase):
    def test_remote_workspace_and_tools_override_client_requests(self):
        message = client_message(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "session/new",
            "params": {"cwd": "/etc", "mcpServers": [{"command": "untrusted"}]}}), Path("/tmp/project"))
        self.assertEqual(message["params"], {"cwd": "/tmp/project", "mcpServers": []})
        message = client_message('{"jsonrpc":"2.0","id":2,"method":"initialize","params":{"clientCapabilities":{"terminal":true}}}', Path("/tmp"))
        self.assertFalse(message["params"]["clientCapabilities"]["terminal"])

    def test_rejects_oversized_and_unknown_methods(self):
        for raw in ["x" * (MAX_FRAME + 1), "[]", '{"jsonrpc":"2.0","method":"exec"}']:
            with self.assertRaises(DefinitionError):
                client_message(raw, Path("/tmp"))


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        release(self.root / "objects")
        self.app = FactoryApp(
            loader=BundleLoader(LocalObjects(self.root / "objects")), state_root=self.root / "state",
            process_factory=lambda: DcodeProcess((sys.executable, str(REPO / "tests/fake_dcode.py"))),
        )

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def connect(self):
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        task = asyncio.create_task(self.app({"type": "websocket", "path": "/ws"}, incoming.get, outgoing.put))
        await incoming.put({"type": "websocket.connect"})
        self.assertEqual((await outgoing.get())["type"], "websocket.accept")
        await self.send(incoming, {"type": "factory.open", "team": "software", "version": "v001"})
        self.assertEqual((await self.receive(outgoing))["type"], "factory.ready")
        return incoming, outgoing, task

    async def send(self, queue, value):
        await queue.put({"type": "websocket.receive", "text": json.dumps(value)})

    async def receive(self, queue):
        event = await asyncio.wait_for(queue.get(), timeout=5)
        return json.loads(event["text"])

    async def rpc(self, incoming, outgoing, request_id, method, params=None):
        await self.send(incoming, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        return await self.receive(outgoing)

    async def test_permission_roundtrip_health_and_resume_across_process_restart(self):
        incoming, outgoing, task = await self.connect()
        try:
            await self.rpc(incoming, outgoing, 1, "initialize")
            result = await self.rpc(incoming, outgoing, 2, "session/new", {"cwd": "/etc"})
            self.assertEqual(result["result"]["sessionId"], "persisted-session")
            approval = await self.rpc(incoming, outgoing, 3, "session/prompt")
            self.assertEqual(approval["method"], "session/request_permission")
            self.assertEqual(self.app.health()["status"], "HealthyBusy")
            timestamp = self.app.health()["time_of_last_update"]
            self.assertEqual(self.app.health()["time_of_last_update"], timestamp)
            await self.send(incoming, {"jsonrpc": "2.0", "id": "permission-1", "result": {"outcome": {"outcome": "cancelled"}}})
            self.assertEqual((await self.receive(outgoing))["method"], "session/update")
            self.assertEqual((await self.receive(outgoing))["id"], 3)
            self.assertEqual(self.app.health()["status"], "Healthy")
        finally:
            await incoming.put({"type": "websocket.disconnect"})
            await asyncio.wait_for(task, timeout=15)
        incoming, outgoing, task = await self.connect()
        try:
            result = await self.rpc(incoming, outgoing, 4, "session/load", {"sessionId": "persisted-session"})
            self.assertTrue(result["result"]["restored"])
            self.assertEqual(result["result"]["cwd"], str((self.root / "state/project").resolve()))
        finally:
            await incoming.put({"type": "websocket.disconnect"})
            await asyncio.wait_for(task, timeout=15)

    async def test_rejects_second_connection_and_releases_lock_after_disconnect(self):
        incoming, outgoing, task = await self.connect()
        sent = []

        async def send(value):
            sent.append(value)

        try:
            await self.app({"type": "websocket", "path": "/ws"}, None, send)
            self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])
        finally:
            await incoming.put({"type": "websocket.disconnect"})
            await asyncio.wait_for(task, timeout=15)
        self.assertFalse(self.app.lock.locked())

    async def test_chunking_stays_below_cloud_frame_limit_and_preserves_unicode(self):
        sent = []

        async def send(value):
            sent.append(value)

        original = {"jsonrpc": "2.0", "method": "session/update", "params": {"text": "😀\n\\" * 50000}}
        await self.app.send_json(send, original)
        self.assertGreater(len(sent), 1)
        self.assertTrue(all(len(value["text"].encode()) <= MAX_FRAME for value in sent))
        combined = "".join(json.loads(value["text"])["data"] for value in sent)
        self.assertEqual(json.loads(combined), original)

    async def test_ping_does_not_require_mounted_storage_or_aws_credentials(self):
        app = FactoryApp(state_root=self.root / "not-mounted")
        sent = []

        async def send(value):
            sent.append(value)

        await app({"type": "http", "path": "/ping", "method": "GET"}, None, send)
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(json.loads(sent[1]["body"])["status"], "Healthy")
        self.assertFalse((self.root / "not-mounted").exists())


if __name__ == "__main__":
    unittest.main()
