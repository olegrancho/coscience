# Dashboard acceptance runbook

Prereqs: `/home/oleg/venvs/coscience` with `.[http]` installed; Node 20+; an authed `claude` CLI for the PM step.

1. **Build the SPA:** `cd frontend && npm install && npm run build`.
2. **Seed a program:** pick a scratch repo dir `$REPO`; run
   `coscience program create --repo $REPO --id demo --title "Demo" --goals "Find X"`.
3. **Serve:** `COSCIENCE_REPO=$REPO coscience-http` (serves API at /api and the SPA at /).
   Open `http://localhost:8000/`.
4. **See the program:** the Programs table shows `demo` (active). Open it; the PM report is empty.
5. **Add a guidance note:** in the program's guidance panel add "prefer cheap in-vitro assays". Confirm it appears.
6. **Run one real PM cycle:** `COSCIENCE_REPO=$REPO coscience pm --repo $REPO --once`.
   Reload the program: the PM report is populated and proposed sprint(s) appear, reflecting the guidance.
7. **Approve from the UI:** open a proposed sprint; click **Approve**; status flips to approved.
8. **Reject from the UI:** propose or pick another proposed sprint; click **Reject**; status flips to canceled.
9. **Run it:** `COSCIENCE_REPO=$REPO coscience dispatch --repo $REPO --once`; a result appears; the sprint reaches done; the result is viewable.
10. **Pause:** click **Pause** on the program; run `coscience pm --repo $REPO --once` again and confirm no new proposals (the PM skips paused programs).

If every step behaves as described, the dashboard closes the human-in-the-loop.
