import { useQuery } from "@tanstack/react-query";
import { api, type CallRow } from "../api";

/** `wiki-ingest` and `wiki-lint` both read as "wiki" in a rail this narrow; the
 *  Compute log carries the precise kind for anyone who needs it. */
function shortKind(kind: string): string {
  if (kind.startsWith("wiki")) return "wiki";
  return kind;
}

function elapsed(startedAt: number | null, now: number): string {
  if (!startedAt) return "";
  const secs = Math.max(0, now / 1000 - startedAt);
  return secs < 90 ? `${Math.round(secs)}s` : `${Math.round(secs / 60)}m`;
}

/** Which agents are calling Claude at this instant, in the rail's pulse.
 *
 *  Distinct from the "running" count above it, which counts sprints in the
 *  executing state: a PM cycle and a wiki ingest spend budget without being
 *  attached to any sprint, so they were invisible until this existed — and on
 *  09-04 it was exactly those unseen calls that took the 5h window to 116%. */
export default function LiveAgents() {
  const log = useQuery({
    queryKey: ["call-log", "live"],
    queryFn: () => api.getCallLog(50),
    refetchInterval: 10_000,
  });

  if (log.isError || !log.data) return null;

  const live: CallRow[] = (log.data.calls ?? [])
    .filter((c) => c.status === "running")
    .sort((a, b) => (a.started_at ?? 0) - (b.started_at ?? 0));

  const now = Date.now();

  return (
    <div style={{ display: "grid", gap: 6, marginTop: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13,
                    color: "var(--ink-muted)" }}>
        <span style={{ width: 9, textAlign: "center",
                       color: live.length ? "var(--machine)" : "var(--ink-faint)" }}>◆</span>
        <span>
          {live.length === 0 ? "no agents running" : (
            <>
              <b className="mono" style={{ color: "var(--ink)" }}>{live.length}</b>
              {live.length === 1 ? " agent" : " agents"} calling Claude
            </>
          )}
        </span>
      </div>

      {live.map((c) => (
        <div key={c.id}
             style={{ display: "flex", justifyContent: "space-between", gap: 8,
                      fontSize: 11, paddingLeft: 17, color: "var(--ink-muted)" }}>
          <span className="mono" style={{ color: "var(--ink)" }}>{shortKind(c.kind)}</span>
          <span className="mono" style={{ flex: 1, overflow: "hidden",
                                          textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {c.program || c.sprint || ""}
          </span>
          <span className="mono">{elapsed(c.started_at, now)}</span>
        </div>
      ))}
    </div>
  );
}
