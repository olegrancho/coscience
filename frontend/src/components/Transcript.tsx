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

export function Transcript({ raw }: { raw: string }) {
  const turns = parseTranscript(raw);
  if (!turns.length) {
    // Not a parseable event stream (plain-text log, or truncated to a fragment
    // with no whole JSON line) — fall back to showing it verbatim.
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
