import asyncio
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from codex_jev.bridge import AuditLog, SessionRouter, codex_environment
from codex_jev.jev import JevClient
from codex_jev.policy import Decision, ROUTES, RoutingError, apply_route, decide, requires_full_context


def response(name="sol-low", confidence=0.9):
    return {"answers": {"route": {"type": "choice", "choice": name, "confidence": confidence}}}


def request(text="Create a PR from these verified changes", request_id=3):
    return {"id": request_id, "method": "turn/start", "params": {
        "threadId": "test-thread", "input": [{"type": "text", "text": text, "text_elements": []}],
        "approvalPolicy": "on-request", "sandboxPolicy": {"type": "readOnly"},
        "collaborationMode": {"mode": "plan", "settings": {
            "model": "original", "reasoning_effort": "high", "developer_instructions": "Preserve me",
        }},
    }}


class PolicyTests(unittest.TestCase):
    def test_three_routes_and_conservative_uncertainty(self):
        for name in ROUTES:
            self.assertEqual(decide(response(name)).route, ROUTES[name])
        self.assertEqual(decide(response(confidence=0.2)).route, ROUTES["sol-medium"])

    def test_invalid_provider_data_cannot_select_arbitrary_model(self):
        for data in [None, {}, response("unexpected"), response(confidence=True),
                     response(confidence=float("nan")), response(confidence=1.1)]:
            with self.subTest(data=data), self.assertRaises(RoutingError):
                decide(data)

    def test_preserves_user_input_permissions_and_collaboration_instructions(self):
        original = request()
        snapshot = deepcopy(original)
        actual = apply_route(original, ROUTES["sol-low"])
        self.assertEqual(original, snapshot)
        expected = deepcopy(original)
        expected["params"].update(model="gpt-5.6-sol", effort="low", serviceTierForTurn="default")
        expected["params"]["collaborationMode"]["settings"].update(model="gpt-5.6-sol", reasoning_effort="low")
        self.assertEqual(actual, expected)

    def test_unseen_context_never_gets_cheap_classification(self):
        for inputs in [[], [{"type": "localImage", "path": "/private/image"}],
                       [{"type": "text", "text": "x" * 4001}]]:
            self.assertTrue(requires_full_context({"input": inputs}))

    def test_child_process_does_not_receive_classifier_key(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret", "JEV_API_KEY": "secret"}):
            self.assertNotIn("TYPESAFE_API_KEY", codex_environment())
            self.assertNotIn("JEV_API_KEY", codex_environment())


class JevTests(unittest.IsolatedAsyncioTestCase):
    async def test_matches_documented_api_and_bounds_context(self):
        with patch("codex_jev.jev.build_opener") as factory:
            factory.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(response()).encode()
            decision = await JevClient("fake-key").classify("x" * 5000, "y" * 2000)
            sent = factory.return_value.open.call_args.args[0]
            body = json.loads(sent.data)
            state = json.loads(body["state"])
            self.assertEqual(len(state["current_request"]), 4000)
            self.assertEqual(len(state["previous_request"]), 1000)
            self.assertEqual(body["questions"]["route"]["type"], "choice")
            self.assertEqual(decision.route.name, "sol-low")

    async def test_http_failure_never_echoes_provider_body_or_key(self):
        with patch("codex_jev.jev.build_opener") as factory:
            factory.return_value.open.side_effect = HTTPError("url", 401, "secret", {}, None)
            with self.assertRaisesRegex(RoutingError, "HTTP 401") as caught:
                await JevClient("secret").classify("hello")
            self.assertNotIn("secret", str(caught.exception))

    async def test_no_key_and_timeout_do_not_fall_back_to_paid_execution(self):
        with self.assertRaises(RoutingError):
            await JevClient("").classify("hello")
        with patch("codex_jev.jev.build_opener") as factory:
            factory.return_value.open.side_effect = TimeoutError
            with self.assertRaises(RoutingError):
                await JevClient("fake-key").classify("hello")


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "events.jsonl"
        self.judge = AsyncMock()
        self.judge.classify.return_value = Decision(ROUTES["sol-medium"], "jev", 0.9)
        self.router = SessionRouter(self.judge, AuditLog(self.log))

    async def test_route_once_then_pass_tools_approvals_and_steering_unchanged(self):
        routed = await self.router.outgoing(request())
        self.assertEqual(routed["params"]["effort"], "medium")
        warning = self.router.incoming({"id": 3, "result": {"turn": {"id": "turn-1"}}})
        self.assertIn("sol", warning["params"]["message"])
        self.router.incoming({"method": "turn/started", "params": {"threadId": "test-thread"}})
        for message in [{"id": "approval", "result": {"decision": "decline"}},
                        {"id": 9, "method": "turn/interrupt", "params": {"threadId": "test-thread"}},
                        request("While doing that, keep the scope narrow", 4)]:
            self.assertEqual(await self.router.outgoing(message), message)
        self.judge.classify.assert_awaited_once()
        self.router.incoming({"method": "turn/completed", "params": {"threadId": "test-thread", "turn": {"status": "completed"}}})
        await self.router.outgoing(request("Now review the migration", 5))
        self.assertEqual(self.judge.classify.await_count, 2)
        self.assertEqual(self.judge.classify.call_args.args[1], request()["params"]["input"][0]["text"])

    async def test_rejected_turn_does_not_update_context(self):
        await self.router.outgoing(request())
        self.assertIsNone(self.router.incoming({"id": 3, "error": {"code": 1}}))
        self.assertEqual(self.router.previous, {})
        self.assertIn("route-rejected", self.log.read_text())

    async def test_structured_background_helpers_preserve_caller_model(self):
        message = request("Name this conversation")
        message["params"]["outputSchema"] = {"type": "object"}
        self.assertEqual(await self.router.outgoing(message), message)
        self.judge.classify.assert_not_awaited()
        self.assertIn("structured-turn-passthrough", self.log.read_text())

    async def test_logs_only_metadata_and_numeric_token_counters(self):
        await self.router.outgoing(request("private-user-text"))
        self.router.incoming({"method": "thread/tokenUsage/updated", "params": {
            "threadId": "test-thread", "turnId": "turn-1",
            "tokenUsage": {"last": {"inputTokens": 12, "outputTokens": 2, "extra": "private-tool-text"}},
        }})
        content = self.log.read_text()
        self.assertNotIn("private-user-text", content)
        self.assertNotIn("private-tool-text", content)
        self.assertIn('"inputTokens": 12', content)
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)

    async def test_attachments_and_manual_mode_do_not_call_jev(self):
        message = request()
        message["params"]["input"].append({"type": "localImage", "path": "/image"})
        routed = (await self.router.outgoing(message))["params"]
        self.assertEqual((routed["model"], routed["effort"]), ("gpt-5.6-sol", "medium"))
        self.router.route = "sol-low"
        self.assertEqual((await self.router.outgoing(request()))["params"]["effort"], "low")
        self.judge.classify.assert_not_awaited()

    async def test_jev_failure_stops_before_creating_pending_turn(self):
        self.judge.classify.side_effect = RoutingError("unavailable")
        with self.assertRaises(RoutingError):
            await self.router.outgoing(request())
        self.assertEqual(self.router.pending, {})


if __name__ == "__main__":
    unittest.main()
