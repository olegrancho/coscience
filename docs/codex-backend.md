# A Codex backend — what it would take (todo H1)

Written 2026-09-13 against `codex-cli 0.154.0` (installed on Avatar at
`~/.local/bin/codex`) and the platform at `6e5ee11`. Finding only — no code.

## Verdict

An adapter, not a rewrite — but the seam has to be cut first. Every agent kind
builds its own `claude` command line and parses Claude Code's stream itself, so
there is no single place a second backend plugs in today. Codex's non-interactive
mode (`codex exec`) covers launch, model, working directory, permissions, JSONL
events, a final-message file and resume by id. What it does not give the platform
for free is the two things Claude Code's stream is load-bearing for: **dollar
cost** per call and the **5h / weekly usage windows** that gate every launch.

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

## The two real gaps

1. **Cost.** Every call row, the Compute tiles and the per-run numbers come from
   `total_cost_usd` in Claude's envelope. A Codex run on a ChatGPT plan is not
   billed per call; at best its events carry token counts, so cost would have to
   be computed from a price table or shown as tokens only.
2. **Usage windows.** The launch gates and the rail's bars read Claude's
   `unifiedWindows` (5h and weekly utilization) from the stream or the usage API.
   Codex draws on a separate quota, so the gate has to become per-backend — and
   whether `codex exec --json` reports any quota reading at all is unknown until a
   real run.

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

## Open until a logged-in run

Needs `codex login` on Avatar, then one `codex exec --json -o last.txt` and one
`codex exec resume` on a throwaway prompt:

- the JSONL event types, and which one carries the session id, token usage and
  any rate-limit or quota reading;
- the exit code and events when the quota is exhausted (the 429 equivalent);
- whether `resume` accepts `--json` and `-o` together;
- whether a detached `codex exec` leaves any process behind after the answer.
