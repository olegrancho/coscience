# Program general instructions

## The problem

A program can tell the PM *what to achieve* (goals) and can send it *individual
steers* (standing guidance threads). It has no way to state house rules — style,
policy, what to never do — that apply to everything the PM writes.

Guidance is the wrong shape for that. Each guidance entry is a thread the PM must
answer via `thread_replies`, and it gets completed and closed. Policy is never
answered and never done.

## The change

A program-level free-text block, written by the human, injected into every PM
prompt as standing house rules.

1. **Storage** — `programs/<id>/instructions.md`, plain prose, no frontmatter
   (mirrors `report.md`). `Substrate.load_instructions` / `save_instructions`.
   Not a `program.md` frontmatter key: that file's body is already goals, and
   YAML is a poor home for multi-paragraph prose.
2. **Context** — `PMContext.instructions: str`, filled in `gather_context`.
3. **Prompts** — one block, emitted only when non-empty, in all three PM prompt
   builders: `pm_claude.render_prompt` (the reasoner), `chat_agent.render_preamble`
   (tool-enabled PM chat) and `pm_claude.render_chat_prompt` (plain chat). It sits
   directly after `PROGRAM GOALS`, above guidance, worded as policy the PM follows
   silently — explicitly not something to reply to, so it is never mistaken for a
   guidance thread.
4. **Trigger** — `"instructions"` joins `_context_payload`, labelled
   `"instructions edited"`, exactly like `"goals edited"`. Editing wakes the PM.
   The key is written **only when the text is non-empty**: the fingerprint is
   persisted per program, so an unconditional key would differ from every stored
   fingerprint the moment this ships and wake every program for a change nobody
   made. Absent-then-present still reads as changed, so the real edit still fires.
5. **API** — `instructions` in the `get_program` payload;
   `POST /programs/{id}/instructions` `{text}` saves, commits the substrate and
   returns the program. Same shape as the existing `/model` and `/workdir` routes.
6. **UI** — a card on the program page above *your guidance to the AI*: renders the
   text, `Edit` swaps in a Textarea with Save/Cancel. A dimmed empty state keeps it
   discoverable when unset.

## Out of scope

Sprint workers. `claude_executor` builds its prompt from the sprint's own goals and
plan; house style does not reach the agents that write the artifacts. Extending it
there is a separate, larger change.
