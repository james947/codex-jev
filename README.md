# Codex Jev router

An opt-in launcher for the normal Codex terminal UI. Jev chooses Sol low or Sol medium when a new chat request starts. Codex handles the conversation, tools, sandbox, and approvals as usual. This prototype does not modify your global Codex configuration.

## Start

Requires Python 3.11+ and Codex CLI. Developed and smoke-tested with Codex 0.155.1 on macOS. The app-server interface is experimental; recheck compatibility after upgrades.

The local installation already has its own `.venv`. From this directory:

```sh
source .venv/bin/activate
codex-jev run --cwd /path/to/your/project
```

Automatic mode requires `TYPESAFE_API_KEY` in the launcher's environment. Obtain a key from the [TypeSafe console](https://console.typesafe.ai/) and load it locally through your preferred secret store. Do not put it in this repository or paste it into chat. The launcher removes that variable from the environment passed to Codex and its tools.

To check a classification without starting a Codex task:

```sh
codex-jev classify 'Create a PR from the already tested changes'
```

To use a fixed route without a Jev key:

```sh
codex-jev run --route sol-low --cwd /path/to/your/project
codex-jev run --route sol-medium --cwd /path/to/your/project
```

Pass additional Codex terminal arguments after `--`. For example, resume through a newly started router:

```sh
codex-jev run --cwd /path/to/your/project -- resume THREAD_ID
```

Only sessions launched with this command use the router. It does not intercept existing sessions or the Codex desktop/IDE interface. Quitting the launcher shuts down its local app-server; its temporary socket will no longer be usable. Ignore the native UI's reconnect command containing that expired socket, and use the resume command above. Finish or interrupt active work before quitting.

## Routing behavior

| Task | Candidate route |
| --- | --- |
| Lookups, explaining small code, renames, known commands, branch/commit/PR for tested work | Sol low |
| Everything else: implementation, fixes, tests, PR review comments, debugging, design | Sol medium |

These descriptions guide Jev's classification; they are not validated measures of model quality. Confidence below 0.6 selects Sol medium conservatively (`FALLBACK_ROUTE` in `policy.py`). That threshold needs evaluation on real tasks and does not express the probability of successful execution. Attachments, additional context, empty text, and requests longer than 4,000 characters also select Sol medium because this classifier cannot evaluate their full contents.

One route applies to the whole turn, including tool continuations. Active-turn steering keeps the current model. There is no automatic retry or escalation after execution starts; stop and resume with `--route sol-medium` when a task needs it. In auto mode the router owns model selection; the native `/model` choice is overridden on the next routed request. `--route` pins the route for that launcher session.

The router applies standard speed and updates both top-level model/effort fields and collaboration-mode settings. It does not alter the prompt, developer instructions, tools, or permissions. A Jev timeout, authentication failure, or invalid response rejects the request visibly before it reaches Codex. It never silently falls back to a paid model after classifier failure.

Submit reviews as normal chat messages, such as “Review this migration for concurrency bugs.” Dedicated `review/start`, goal-setting, and queue-start RPCs are not supported by this prototype. Structured-output turns (including Codex's background naming helpers) pass through with their original settings and are logged separately. Internal agent calls and other work started inside app-server are not routed individually. Model availability and account limits remain Codex's responsibility.

## Data and measurement

Automatic mode sends the current request text (up to 4,000 characters) and the previous accepted request in the same router session (up to 1,000 characters) to TypeSafe. It does not read repository files or transmit tool results or the whole conversation. Text you paste into your request is included, so do not paste credentials into a routed prompt. A resumed session has no previous-request excerpt until its first accepted routed turn; ambiguous requests should therefore receive Sol medium.

Codex retains the full original conversation. The router sits before app-server, so it never handles OpenAI authentication tokens itself. The local WebSocket uses a Unix socket in a private temporary directory. There is no TCP listener or external-model fallback.

Route metadata and numeric token counters are appended to:

```text
~/.local/state/codex-jev-router/decisions.jsonl
```

The file is created with mode 0600. It contains no prompt or tool-result text. `route-selected` means the router chose a route; `route-accepted` means app-server accepted the request, not that generation succeeded. Check `turn-completed` status for the outcome. Codex may independently reroute, which is marked separately.

Token events contain snapshots: `total` is cumulative and repeated events must not be summed. `last` can cover more than one model call within a turn. These counters do not measure weekly quota debit or prove savings. Compare completed task quality, corrections, latency, and the account's usage display before adopting this as a default. Jev has its own provider charges. Background Codex helpers can also consume tokens outside normal user turns.

## Validation

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The tests cover routing decisions, malformed responses, classification failures, request preservation, collaboration-mode precedence, credentials excluded from child environments, metadata-only logging, and a real Unix-socket/subprocess approval round trip using a fake backend.

On September 21, 2026 the real Codex terminal UI connected through the router, accepted Sol low, and completed a one-line response smoke test. That run also observed Codex's background naming turn. Live Jev classification and live switching between Sol and Astra remain unverified until a TypeSafe key is supplied. No measured weekly-quota saving is claimed.

## Sources

- [Codex app-server documentation](https://developers.openai.com/codex/app-server)
- Local schemas generated by `codex app-server generate-ts` from CLI 0.155.1
- [TypeSafe API quick start](https://docs.typesafe.ai/introduction/quickstart)
- [TypeSafe Choice response contract](https://docs.typesafe.ai/primitives/choice)

The implementation uses Codex's native app-server protocol rather than the separate Codex Router stack used by the community Jev router.
