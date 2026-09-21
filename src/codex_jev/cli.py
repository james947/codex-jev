"""Opt-in launcher. Never changes Codex's global configuration."""

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from websockets.asyncio.server import unix_serve

from codex_jev.bridge import AuditLog, SessionRouter, bridge, codex_environment
from codex_jev.jev import JevClient
from codex_jev.policy import ROUTES, RoutingError


async def launch(args: argparse.Namespace) -> int:
    codex = shutil.which("codex")
    if not codex:
        raise RoutingError("codex was not found on PATH.")
    if args.route == "auto" and not os.environ.get("TYPESAFE_API_KEY"):
        raise RoutingError("Set TYPESAFE_API_KEY locally, or use --route sol-low/sol-medium.")
    audit = AuditLog(Path(args.log).expanduser())
    judge = JevClient(os.environ.get("TYPESAFE_API_KEY", ""))
    cwd = str(Path(args.cwd).expanduser().resolve(strict=True))
    # A private socket avoids an unauthenticated TCP endpoint for Codex operations.
    with tempfile.TemporaryDirectory(prefix="codex-jev-", dir="/tmp") as runtime:
        socket_path = str(Path(runtime) / "router.sock")
        connected = False

        async def handle(connection):
            nonlocal connected
            if connected:
                await connection.close(code=1008, reason="One client per launcher")
                return
            connected = True
            try:
                await bridge(connection, [codex, "app-server"], SessionRouter(judge, audit, args.route), cwd)
            finally:
                connected = False

        async with unix_serve(handle, socket_path, origins=[None], max_size=64 * 1024 * 1024):
            os.chmod(socket_path, 0o600)
            extra = args.codex_args
            if extra and extra[0] == "--":
                extra = extra[1:]
            if any(value == "--remote" or value.startswith("--remote=") for value in extra):
                raise RoutingError("The launcher owns --remote; do not override it.")
            print(f"Jev router: {args.route}. Decision log: {audit.path}", file=sys.stderr)
            if args.route == "auto":
                print("TypeSafe receives bounded current/previous request text for classification.", file=sys.stderr)
            # Remote mode needs an explicit workspace, e.g. for `resume` with tui.resume_cwd.
            has_cd = any(v in ("-C", "--cd") or v.startswith("--cd=") for v in extra)
            workspace = [] if has_cd else ["--cd", str(cwd)]
            child = await asyncio.create_subprocess_exec(
                codex, "--remote", f"unix://{socket_path}", *workspace, *extra,
                cwd=cwd, env=codex_environment(),
            )
            try:
                return await child.wait()
            finally:
                if child.returncode is None:
                    child.terminate()
                    await child.wait()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Open the normal Codex terminal UI through the router")
    run.add_argument("--route", choices=["auto", *ROUTES], default="auto")
    run.add_argument("--cwd", default=os.getcwd())
    run.add_argument("--log", default="~/.local/state/codex-jev-router/decisions.jsonl")
    run.add_argument("codex_args", nargs=argparse.REMAINDER, help="Codex arguments after --")
    preview = commands.add_parser("classify", help="Ask Jev for a route without starting Codex")
    preview.add_argument("text")
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "classify":
            if len(args.text) > 4000:
                raise RoutingError("Use a task description of at most 4000 characters.")
            decision = asyncio.run(JevClient(os.environ.get("TYPESAFE_API_KEY", "")).classify(args.text))
            print(json.dumps(asdict(decision), indent=2))
        else:
            sys.exit(asyncio.run(launch(args)))
    except (RoutingError, FileNotFoundError) as exc:
        print(f"codex-jev: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
