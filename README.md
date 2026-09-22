# codex-jev

My Codex limits kept running out way too fast. Half my prompts were stuff like "where's this function" or "what does this do", and they were getting the same effort as real work.

So I vibecoded a little router. Every prompt goes through [Jev](https://typesafe.ai) first, and Jev decides how much brain it actually needs:

```
"what does build_state do?"             → Sol low
"find the run_turn method"              → Sol low
"commit and push the tested changes"    → Sol low
"fix the failing lint in cli.py"        → Sol medium
"why are users getting duplicate SMS?"  → Sol medium
```

It's still just Codex. Same UI, same tools, same approvals. I type `codex` like always, and a little line above each reply tells me where it went:

```
⚠ Jev router: gpt-5.6-sol / low
```

If Jev isn't sure, or you attach something it can't see (like a screenshot), it plays it safe and uses Sol medium.

## Setup

You need Python 3.11+, the Codex CLI, and a TypeSafe key from [console.typesafe.ai](https://console.typesafe.ai).

```sh
git clone https://github.com/james947/codex-jev.git && cd codex-jev
python3 -m venv .venv && .venv/bin/pip install -e .
export TYPESAFE_API_KEY=...
.venv/bin/codex-jev run --cwd /path/to/your/project
```

Want plain `codex` to go through it? Drop this in your `~/.zshrc`:

```zsh
function codex() {
  case "$1" in
    exec|e|login|logout|mcp|mcp-server|app-server|completion|sandbox|debug|apply|cloud|features|update|doctor|help|--help|-h|--version|-V)
      command codex "$@" ;;
    *)
      /path/to/codex-jev/.venv/bin/codex-jev run --cwd "$PWD" -- "$@" ;;
  esac
}
```

`command codex` skips the router whenever you want the old behavior.

## Handy stuff

```sh
codex-jev classify "rename this variable to user_id"   # see what Jev would pick
codex-jev run --route sol-medium --cwd .              # skip Jev, pin a route
codex resume <thread-id>                              # resume works as normal
```

Want to tweak the routes? It's all in [`src/codex_jev/policy.py`](src/codex_jev/policy.py): the route list, the descriptions Jev reads, and the fallback.

## Good to know

- **Jev only sees your messages.** It gets your current prompt and your previous one, not Codex's replies, your files, or tool output. So vague follow-ups like "yes do it" can land on medium.
- **Switching effort takes a small cache hit, not a full one.** sol-low and sol-medium are the same model, so a route change doesn't wipe the cache the way switching models would. It just reads a bit less from it on that turn.
- **Your key stays out of Codex.** The launcher strips `TYPESAFE_API_KEY` before starting Codex.
- **There's a log.** Routes and token counts (no prompt text) go to `~/.local/state/codex-jev-router/decisions.jsonl`.
- **It's a prototype.** It relies on Codex's experimental app-server protocol (tested on Codex 0.155.1, macOS), so a Codex update could break it. I haven't measured the actual savings yet, it just feels a lot better.
- **When you quit**, Codex prints a reconnect command with an old socket in it. Ignore it and use `codex resume <id>`.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```
