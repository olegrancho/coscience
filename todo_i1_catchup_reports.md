# I1 — catch-up reports: what the manual version looks like

The catch-up report automates something already done by hand: about once a week per
active program, a chat with the planner titled "Update <date>" or "Brief <date>". This
file records what those chats asked for and what made the answers useful, so the
report is built to the same shape.

## What gets asked

- "What was accomplished in the past couple of weeks, and where do things stand now?
  Clear and concise, and your ideas on what to do next."
- "I haven't looked since <date>. First an overview as of <date>, then what changed."
  The reader's last look is the baseline, and it is not always the last report.
- A scoped variant: "summarise the recent results along <one axis of the program>."
- "Use markdown links when you reference a sprint" — asked again and again (that is K1).
- Plain words, no jargon, self-contained — asked of reports and artifacts alike.

## The answer shape that worked

1. **Bottom line first**, in one or two bold sentences, including what is now out of
   date (a story the program told earlier that no longer holds).
2. **The key numbers in one table**, each against the baseline the program measures
   itself by, with whether the difference is significant.
3. **What the period delivered**: results that moved things, clean negatives that
   narrowed the search, and blockers — each tied to its sprints.
4. **Where the program stands overall** against its goals.
5. **What to do next, numbered and in order**, each with why and roughly what it
   costs. The numbers matter: follow-ups say "explain #3", "promote #3 to a sprint".
6. **Housekeeping**: stale or stuck sprints, artifacts and docs that the new results
   have made out of date.

Replies ran 2,000–7,000 characters; a short program needs the short version.

## What happens after reading

The follow-ups are actions, not more reading: explain one item, promote an idea to a
sprint, add ideas to the pool, brainstorm neighbours of a promising idea, drill into
one sprint. So the report page should carry a "continue in chat" that opens a chat
already holding the report, where those follow-ups work as they do today.

## Implications for the build

- The report covers the time since the previous report; generating one on demand can
  take an explicit "since" date for a reader who has been away longer.
- Mark reports this browser has not opened yet, the way new experiments are marked.
- Every sprint and idea the report names is a link (K1).
