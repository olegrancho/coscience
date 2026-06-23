# Phase 0 — Manual Acceptance Runbook

Proves the walking skeleton runs end-to-end with a real Claude Code agent and
survives a kill. Run on avatar (or any host with the `claude` CLI logged in).

## Setup
1. Create a substrate repo:
   - `mkdir -p /tmp/coscience-demo && cd /tmp/coscience-demo && git init`
   - `git config user.email demo@local && git config user.name demo`
2. Create `sprints/demo/sprint.md`:
   ```
   ---
   status: approved
   goals: Produce a one-paragraph literature-style note on a trivial topic.
   plan:
     - id: s1
       run: "Write the file note.md in the current directory containing one paragraph about why checkpointing matters. Then print DONE."
   ---
   # Sprint demo
   ```

## Run with the real agent
- From the coscience project: edit `run_once` (or add a flag) to use
  `ClaudeCodeExecutor()` instead of `ShellStepExecutor()`, OR run a short
  Python REPL:
  ```python
  from pathlib import Path
  from coscience.substrate import Substrate
  from coscience.worker import Worker
  from coscience.claude_executor import ClaudeCodeExecutor
  w = Worker(Substrate(Path("/tmp/coscience-demo")), ClaudeCodeExecutor())
  print(w.run_one_beat())  # PROGRESSED
  print(w.run_one_beat())  # COMPLETED
  ```

## Acceptance checks
- [ ] `results/demo-result.md` exists and the sprint status is `done`.
- [ ] `git log` in the substrate shows a commit per checkpoint.
- [ ] **Kill test:** add a second long step (`detached: sleep 60; ...`), run one
      beat, `kill -9` the python process, start a fresh REPL, keep beating —
      confirm the sprint completes and no step ran twice.
