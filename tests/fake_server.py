"""Deterministic app-server fixture; never calls a model or executes tools."""

import json
import sys


def send(message):
    print(json.dumps(message), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "turn/start":
        send({"id": message["id"], "result": {"received": message["params"]}})
        send({"method": "turn/started", "params": {"threadId": "test-thread", "turn": {"id": "t"}}})
        send({"id": "approval-1", "method": "item/commandExecution/requestApproval", "params": {"threadId": "test-thread"}})
    elif message.get("id") == "approval-1":
        send({"method": "test/approval", "params": message["result"]})
        send({"method": "turn/completed", "params": {"threadId": "test-thread", "turn": {"id": "t", "status": "completed"}}})
    else:
        send({"id": message.get("id"), "result": {"echo": message}})
