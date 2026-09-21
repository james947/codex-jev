"""WebSocket-to-stdio bridge with routing only at new user-turn boundaries."""

import asyncio
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from codex_jev.jev import JevClient
from codex_jev.policy import (
    FALLBACK_ROUTE, Decision, ROUTES, RoutingError, apply_route, request_text,
    requires_full_context,
)


class AuditLog:
    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path

    def emit(self, event: str, **fields: Any) -> None:
        entry = {"time": time.time(), "event": event, **fields}
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as output:
            output.write(json.dumps(entry, allow_nan=False) + "\n")


def codex_environment() -> dict[str, str]:
    """Do not expose the classifier credential to Codex or its tool processes."""
    return {key: value for key, value in os.environ.items() if key not in {"TYPESAFE_API_KEY", "JEV_API_KEY"}}


class SessionRouter:
    def __init__(self, judge: JevClient, audit: AuditLog, route: str = "auto"):
        self.judge = judge
        self.audit = audit
        self.route = route
        self.active: set[str] = set()
        self.previous: dict[str, str] = {}
        self.pending: dict[str | int, tuple[str, str, Decision]] = {}

    async def outgoing(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method")
        if method in {"review/start", "thread/goal/set", "thread/queue/start"}:
            raise RoutingError("This prototype routes normal chat turns. Submit the task as a chat message.")
        if method != "turn/start":
            return message
        params = message.get("params", {})
        thread = params.get("threadId", "")
        # Codex's background structured helpers are not ordinary user requests.
        # Preserve their caller-selected model instead of sending them to Jev.
        if params.get("outputSchema") is not None:
            self.audit.emit("structured-turn-passthrough", request_id=message.get("id"), thread_id=thread)
            return message
        # A turn/start while already running is steering, not a new model decision.
        if thread in self.active or params.get("toolOutput") is not None:
            return message
        start = time.monotonic()
        text = request_text(params)
        if self.route != "auto":
            decision = Decision(ROUTES[self.route], "manual")
        elif requires_full_context(params):
            decision = Decision(ROUTES[FALLBACK_ROUTE], "context-not-visible-to-jev")
        else:
            decision = await self.judge.classify(text, self.previous.get(thread, ""))
        request_id = message["id"]
        self.pending[request_id] = (thread, text[:1000], decision)
        self.audit.emit(
            "route-selected", request_id=request_id, thread_id=thread,
            **asdict(decision), latency_ms=round((time.monotonic() - start) * 1000),
        )
        return apply_route(message, decision.route)

    def incoming(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Observe backend acceptance and usage; return an optional UI notification."""
        request_id = message.get("id")
        pending = self.pending.pop(request_id, None) if "method" not in message else None
        if pending is not None:
            thread, text, decision = pending
            accepted = "result" in message
            self.audit.emit(
                "route-accepted" if accepted else "route-rejected",
                request_id=request_id, thread_id=thread, route=decision.route.name,
            )
            if accepted:
                self.previous[thread] = text
                return {
                    "method": "warning",
                    "params": {"threadId": thread, "message": (
                        f"Jev router: {decision.route.model} / {decision.route.effort} "
                        f"({decision.source}; standard speed)."
                    )},
                }
        method = message.get("method")
        params = message.get("params", {})
        thread = params.get("threadId", "")
        if method == "turn/started":
            self.active.add(thread)
        elif method == "turn/completed":
            self.active.discard(thread)
            self.audit.emit("turn-completed", thread_id=thread, status=params.get("turn", {}).get("status"))
        elif method == "thread/tokenUsage/updated":
            # Only numeric counters; never copy an arbitrary provider payload to disk.
            counters = {
                bucket: {key: value for key, value in values.items() if type(value) is int}
                for bucket, values in params.get("tokenUsage", {}).items()
                if bucket in {"last", "total"} and isinstance(values, dict)
            }
            self.audit.emit("token-usage", thread_id=thread, turn_id=params.get("turnId"), counters=counters)
        elif method == "model/rerouted":
            self.audit.emit("backend-rerouted", thread_id=thread)
        return None


async def bridge(websocket, command: list[str], router: SessionRouter, cwd: str) -> None:
    child = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, cwd=cwd, env=codex_environment(),
        limit=64 * 1024 * 1024,
    )

    async def to_codex() -> None:
        async for raw in websocket:
            message = json.loads(raw)
            try:
                routed = await router.outgoing(message)
            except RoutingError as exc:
                router.audit.emit("routing-error", request_id=message.get("id"))
                await websocket.send(json.dumps({
                    "id": message.get("id"), "error": {"code": -32000, "message": str(exc)},
                }))
                continue
            child.stdin.write(json.dumps(routed).encode() + b"\n")
            await child.stdin.drain()

    async def from_codex() -> None:
        while raw := await child.stdout.readline():
            extra = router.incoming(json.loads(raw))
            await websocket.send(raw.decode().rstrip("\n"))
            if extra:
                await websocket.send(json.dumps(extra))

    tasks = [asyncio.create_task(to_codex()), asyncio.create_task(from_codex())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), timeout=5)
            except TimeoutError:
                child.kill()
                await child.wait()
        await websocket.close()
