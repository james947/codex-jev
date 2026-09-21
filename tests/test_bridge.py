import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve

from codex_jev.bridge import AuditLog, SessionRouter, bridge
from codex_jev.policy import Decision, ROUTES


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_socket_and_subprocess_preserve_bidirectional_protocol(self):
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="jtest-") as directory:
            path = str(Path(directory) / "s")
            judge = AsyncMock()
            judge.classify.return_value = Decision(ROUTES["sol-low"], "jev", 0.99)
            router = SessionRouter(judge, AuditLog(Path(directory) / "log"))
            finished = asyncio.Event()

            async def handle(connection):
                try:
                    await bridge(connection, [sys.executable, str(Path(__file__).with_name("fake_server.py"))], router, directory)
                finally:
                    finished.set()

            async with unix_serve(handle, path):
                async with unix_connect(path) as client:
                    await client.send(json.dumps({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "test"}}}))
                    self.assertEqual(json.loads(await client.recv())["id"], 0)
                    params = {"threadId": "test-thread", "input": [{"type": "text", "text": "Create a branch"}], "approvalPolicy": "on-request"}
                    await client.send(json.dumps({"id": 1, "method": "turn/start", "params": params}))
                    result = json.loads(await client.recv())
                    self.assertEqual(result["result"]["received"]["model"], "gpt-5.6-sol")
                    self.assertEqual(result["result"]["received"]["input"], params["input"])
                    while True:
                        event = json.loads(await asyncio.wait_for(client.recv(), 3))
                        if event.get("id") == "approval-1":
                            break
                    await client.send(json.dumps({"id": "approval-1", "result": {"decision": "decline"}}))
                    event = json.loads(await client.recv())
                    self.assertEqual(event, {"method": "test/approval", "params": {"decision": "decline"}})
                    self.assertEqual(json.loads(await client.recv())["method"], "turn/completed")
                await asyncio.wait_for(finished.wait(), 5)
            judge.classify.assert_awaited_once()
