import { Stack, Text } from "@mantine/core";

type Turn = { kind: "say" | "tool" | "result"; text: string };

/** Parse a Claude stream-json event feed into a readable transcript: what it
 *  said, which tools it ran, and the final result line. Unknown/noisy events are
 *  dropped so the live log reads like a narrative rather than raw JSONL. */
export function parseTranscript(raw: string): Turn[] {
  const turns: Turn[] = [];
  for (const line of raw.split("\n")) {
    const t = line.trim();
    if (!t.startsWith("{")) continue;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let ev: any;
    try { ev = JSON.parse(t); } catch { continue; }
    if (ev.type === "assistant") {
      for (const b of ev.message?.content ?? []) {
        if (b.type === "text" && b.text?.trim()) turns.push({ kind: "say", text: b.text.trim() });
        else if (b.type === "tool_use") {
          const inp = b.input ?? {};
          const arg = inp.command || inp.file_path || inp.path || inp.pattern || inp.description || "";
          turns.push({ kind: "tool", text: `${b.name}${arg ? ": " + String(arg).split("\n")[0] : ""}` });
        }
      }
    } else if (ev.type === "result") {
      const cost = typeof ev.total_cost_usd === "number" ? ` · $${ev.total_cost_usd.toFixed(2)}` : "";
      turns.push({ kind: "result", text: `finished${cost}` });
    }
  }
  return turns;
}

/** True if raw contains at least one parseable stream-json event line. Lets us tell
 *  a warming-up event stream (only a `system`/`init` event so far → nothing to render
 *  yet) apart from a genuine plain-text log we should show verbatim. */
function hasJsonEvents(raw: string): boolean {
  for (const line of raw.split("\n")) {
    const t = line.trim();
    if (!t.startsWith("{")) continue;
    try { const ev = JSON.parse(t); if (ev && typeof ev.type === "string") return true; } catch { /* not JSON */ }
  }
  return false;
}

export function Transcript({ raw }: { raw: string }) {
  const turns = parseTranscript(raw);
  if (!turns.length) {
    // A valid event stream with nothing to show yet (e.g. only the init event on the
    // first poll) — show a placeholder, NOT the raw JSON. Only fall back to verbatim
    // for genuine plain-text logs with no JSON events at all.
    if (hasJsonEvents(raw)) return <Text size="sm" c="dimmed">Starting up — no agent activity yet.</Text>;
    return raw.trim()
      ? <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
             fontSize: 12.5, lineHeight: 1.5, maxHeight: 440, overflow: "auto" }}>{raw}</pre>
      : <Text size="sm" c="dimmed">Starting up — no agent activity yet.</Text>;
  }
  return (
    <Stack gap={7} style={{ maxHeight: 460, overflowY: "auto", overflowX: "hidden" }}>
      {turns.map((t, i) => t.kind === "say" ? (
        <Text key={i} size="sm" style={{ lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{t.text}</Text>
      ) : t.kind === "result" ? (
        <Text key={i} size="xs" className="mono" style={{ color: "var(--machine)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>✓ {t.text}</Text>
      ) : (
        <Text key={i} size="xs" className="mono" style={{ color: "var(--ink-faint)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>▸ {t.text}</Text>
      ))}
    </Stack>
  );
}
