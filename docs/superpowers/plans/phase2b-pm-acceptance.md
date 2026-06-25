# Phase 2b — PM agent live acceptance runbook

Prereqs: the `claude` CLI is installed and authenticated; run from the repo root;
use a scratch repo dir, e.g. `R=/tmp/coscience-pm-demo`. Test interpreter/venv:
`/home/oleg/venvs/coscience/bin/coscience` (the `coscience` console script).

1. Create a program:
   `coscience program create --repo $R --id p1 --title "Demo" --goals "Find the smallest prime gap above 1e6 by brute force"`
2. Run ONE real PM cycle (this calls the live `claude`):
   `coscience pm --repo $R --once`
   -> prints `p1: cycle=0 submitted=[...]`; the model proposed at least one sprint.
3. Inspect what it proposed (machinery wrote it, status must be `proposed`):
   `coscience` has no read cmd — use the HTTP API: in another shell,
   `COSCIENCE_REPO=$R COSCIENCE_HOST=127.0.0.1 coscience-http` then
   `curl -s localhost:8000/programs/p1 | jq` (see the report + sprint list) and
   `curl -s 'localhost:8000/sprints?status=proposed' | jq`.
4. Read the report the PM wrote: `cat $R/programs/p1/report.md`.
5. Approve one proposed sprint (human gate):
   `curl -s -X POST localhost:8000/sprints/<sprint-id>/approve`  -> status "approved".
6. Run the dispatcher to execute it:
   `coscience dispatch --repo $R --once`  (add `--executor claude` only if a step needs the LLM).
   Re-run `--once` until the sprint reaches `done` and a result file appears under `$R/results/`.
7. Run the PM again so it REACTS to the new result:
   `coscience pm --repo $R --once`
   -> cycle=1; the new report references the completed work; any follow-up proposal avoids repeating prior ones.
8. Confirm the loop closed: `cat $R/programs/p1/pm.md` shows cycle=2 and the proposed-id history.

If the model returns malformed JSON, the beat raises PMReasonerError, stages nothing,
and the next `coscience pm --once` simply retries — no partial state is written.
