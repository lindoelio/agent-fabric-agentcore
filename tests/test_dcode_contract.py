"""Opt-in smoke test of real pinned dcode, without invoking a paid model."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_factory.bundles import BundleLoader, LocalObjects
from agent_factory.process import DcodeProcess
from test_factory import release


@unittest.skipUnless(os.getenv("FACTORY_TEST_REAL_DCODE") == "1", "Requires installed dcode; enabled in CI")
class DcodeContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_new_session_and_persistent_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            objects = root / "objects"
            release(objects)
            bundle = BundleLoader(LocalObjects(objects)).materialize(root / "state", "software", "v001")
            # Replace the fixture model with a real Bedrock ID. No prompt is sent.
            from dataclasses import replace

            bundle = replace(bundle, model="bedrock:us.anthropic.claude-sonnet-4-5-20250929-v1:0")
            session_id = None
            env = {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_REGION": "us-west-2", "AWS_DEFAULT_REGION": "us-west-2",
                   "AWS_EC2_METADATA_DISABLED": "true"}
            with patch.dict(os.environ, env):
                for resume in (False, True):
                    process = DcodeProcess()
                    await process.start(root / "state", bundle)
                    stderr = asyncio.create_task(process.drain_stderr())
                    try:
                        await process.write({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                            "protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
                        }})
                        result = await asyncio.wait_for(process.read(), 90)
                        self.assertIsNotNone(result, "dcode exited before initializing ACP")
                        self.assertNotIn("error", result)
                        params = {"cwd": str(root / "state/project"), "mcpServers": []}
                        if resume:
                            params["sessionId"] = session_id
                        await process.write({"jsonrpc": "2.0", "id": 2,
                            "method": "session/load" if resume else "session/new", "params": params})
                        while True:
                            result = await asyncio.wait_for(process.read(), 90)
                            self.assertIsNotNone(result)
                            if result.get("id") == 2 and "method" not in result:
                                break
                        self.assertNotIn("error", result)
                        if not resume:
                            session_id = result["result"]["sessionId"]
                    finally:
                        await process.stop()
                        stderr.cancel()
                        await asyncio.gather(stderr, return_exceptions=True)
