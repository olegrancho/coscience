# A Codex backend — what it would take (todo H1)

Written 2026-09-13 against `codex-cli 0.154.0` (installed on Avatar at
`~/.local/bin/codex`) and the platform at `6e5ee11`. Finding only — no code.

## Verdict

An adapter, not a rewrite — but the seam has to be cut first. Every agent kind
builds its own `claude` command line and parses Claude Code's stream itself, so
there is no single place a second backend plugs in today. Codex's non-interactive
mode (`codex exec`) covers launch, model, working directory, permissions, JSONL
events, a final-message file and resume by id. Of the two things Claude Code's
stream is load-bearing for, Codex has one in a different place and lacks the other:
its **5h / weekly usage windows** exist but live in its session file, and there is
no **dollar cost** per call, only tokens.

## Where the platform assumes `claude`

| Site | What it builds or reads | Claude-specific |
|---|---|---|
| Worker — `claude_executor.ClaudeAgent` | detached `claude -p` per sprint; `--resume <sid>` nudge after an ambiguous exit; `collect` unwraps the final envelope into the result text and the `agent.cost.json` sidecar; `read_activity` labels live events for the dashboard | `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, `--disallowedTools Monitor`, `--dangerously-skip-permissions`, `--output-format stream-json --verbose`, `session_id` in events |
| Wiki — `wiki_agent.WikiAgent` | detached ingest/lint run; `read_outcome` reads cost, turns, tokens, costliest model, 429 vs failure, rate-limit readings | `--tools Read,Edit,Write,Bash`, `modelUsage`, `is_error` + `api_error_status`, `rate_limit_event` |
| Chat — `chat_agent` | detached turn per message; `--session-id` chosen up front, `--resume` after; read-only scope as an `--allowedTools` list | pre-assigned session ids; tool allowlist as the sandbox |
| PM — `pm_claude` | synchronous cycle on stdin; JSON decoded out of the final text; `chat_reply` in text mode; `draft_sprint` with `--tools Read,Glob,Grep` | final `result` envelope, `total_cost_usd` |
| Wiki probe — `wiki_probe` | read-only answer, then `--resume` debrief | same |
| Shared parsers — `agent_stream`, `usage_meter` | `parse_stream`, `parse_rate_limits`, `token_breakdown`, `record_limits`, `five_hour_window` | Claude's event and envelope shapes; `unifiedWindows` utilization |
| Usage gate — worker, wiki and PM launch gates; the rail's usage bars | the recorded Claude reading, falling back to the usage skill (Claude OAuth API) | a Claude subscription's windows |
| Models — `MODEL_OPTIONS`, `DEFAULT_MODEL`, `ModelSelect` | model slugs | Claude slugs only |

## Flag-by-flag mapping

| Need | Claude Code | Codex (`codex exec`) |
|---|---|---|
| headless run, prompt on stdin | `claude -p` + stdin | `codex exec -` (stdin) |
| event feed | `--output-format stream-json --verbose` | `--json` (JSONL) |
| final answer | the `result` envelope's `result` | `-o, --output-last-message <file>` — simpler to collect |
| model | `--model` | `-m, --model` |
| working directory | process cwd | `-C, --cd <dir>` (and `--skip-git-repo-check` outside a git repo) |
| full autonomy (worker, wiki, full-scope chat) | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` |
| read-only (chat read scope, PM draft, probe) | a tool allowlist | `-s read-only` — a real sandbox, stronger than an allowlist |
| restrict tool schemas (the J1 cost cut) | `--tools` | no equivalent; sandbox modes instead |
| resume a session | `--resume <sid>` | `codex exec resume <SESSION_ID> [PROMPT]` |
| choose the session id up front | `--session-id <uuid>` | none — capture it from the first turn's events |
| structured final output | parse JSON out of text | `--output-schema <file>` — could replace the PM's JSON decoding |
| no background work outliving the run | env var + `--disallowedTools Monitor` | not needed as far as the help shows; confirm on a real run |

## The gaps

1. **Cost — real.** Every call row, the Compute tiles and the per-run numbers come
   from `total_cost_usd` in Claude's envelope. A Codex run on a ChatGPT plan is not
   billed per call and its events carry token counts only, so Compute would show
   tokens, or a cost computed from a price table.
2. **Usage windows — smaller than feared, but per-backend.** The launch gates and
   the rail's bars read Claude's `unifiedWindows`. Codex keeps its own 5h and weekly
   windows (`used_percent`, `resets_at`) — the same shape — but only in its session
   file, not in the `--json` stream. The gate has to read the right quota for the
   agent it is about to launch.

## Suggested seam

One `AgentBackend` interface that the three existing classes implement for Claude
and a new one implements for Codex:

- `command(kind, prompt_file, model, cwd, scope, resume_id) -> (argv, env)`
- `parse(raw_events) -> StreamResult(text, session_id, cost, tokens, usage, turns,
  model, status, limits)`
- `label(event) -> str` for the live-activity line

The worker, wiki, chat and PM keep their lifecycle logic and stop knowing flag
names; the call log and the gate consume `StreamResult` instead of Claude's
envelope. A `backend` choice would then sit beside each model setting (per agent
kind per program, matching H4/H5). Rough size: the seam is a day or two touching
six modules and their tests; the Codex implementation is small once the event
schema below is known.

## What a real run showed (2026-09-13 22:29, ChatGPT Plus login)

`echo "…" | codex exec --json -s read-only --skip-git-repo-check -o last.txt -` in a
scratch dir took 7s, exited 0 and wrote the answer to `last.txt`. Then
`codex exec resume <thread_id> --json -o last2.txt -` continued the same thread,
also exit 0. No process was left behind.

- **Stream (`--json`)** is four event types: `thread.started` (carries `thread_id`,
  the id `resume` takes), `turn.started`, `item.completed` (`item.type` =
  `agent_message`, with `text`), and `turn.completed` with `usage`:
  `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
  `output_tokens`, `reasoning_output_tokens`. No cost, no model name, no quota.
  Resume re-emits the same `thread_id`, and its usage is cumulative for the thread.
- **Session file** `~/.codex/sessions/YYYY/MM/DD/rollout-…-<thread_id>.jsonl` has
  what the stream omits: the model (`gpt-6-astra` by default here), `duration_ms`,
  and on each `token_count` event a `rate_limits` block —
  `primary {used_percent, window_minutes: 300, resets_at}`,
  `secondary {used_percent, window_minutes: 10080, resets_at}`, `plan_type`, and
  `rate_limit_reached_type`. That is the Codex equivalent of `unifiedWindows`.

Still unknown: the exit code and events when the quota is actually exhausted.
`rate_limit_reached_type` is the field to watch.
