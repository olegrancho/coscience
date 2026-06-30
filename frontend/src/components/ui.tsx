import type { CSSProperties, ReactNode } from "react";
import { Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { RunAgg, Usage } from "../api";
import { SPRINT_STATE_ORDER, statusVar } from "./status";

/** "2h ago" / "3d ago" / "Jun 27" — with the exact local time on hover. */
function relTime(at: number): string {
  const s = Math.max(0, Date.now() / 1000 - at);
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function RelTime({ at, prefix }: { at?: number | null; prefix?: string }) {
  if (!at) return null;
  const abs = new Date(at * 1000).toLocaleString();
  return <span title={abs}>{prefix}{relTime(at)}</span>;
}

/** A clear way back to the parent this page belongs under. Names the parent so
 *  it doubles as context ("‹ Demo" tells you the experiment is in the Demo program). */
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="backlink">
      <span aria-hidden>‹</span> <span className="backlink-label">{children}</span>
    </Link>
  );
}

type Var = CSSProperties & Record<string, string | number>;

/** Status pill — a mono uppercase chip tinted by the status hue. */
export function StatusBadge({ status }: { status: string }) {
  return (
    <span className="pill" style={{ "--st": statusVar(status) } as Var}>
      <span className="dot" />
      {status}
    </span>
  );
}

/** A pulsing teal dot: this program is live and the PM is cycling it. */
export function Heartbeat() {
  return <span className="heartbeat" aria-label="active" title="active" />;
}

/** Segmented bar showing the distribution of a program's sprint states. */
export function StateBar({ counts }: { counts: Record<string, number> }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return (
    <div className="statebar" aria-hidden>
      {SPRINT_STATE_ORDER.filter((s) => counts[s]).map((s) => (
        <span
          key={s}
          title={`${s}: ${counts[s]}`}
          style={{ width: `${(counts[s] / total) * 100}%`, background: statusVar(s) }}
        />
      ))}
    </div>
  );
}

/** A labelled capacity gauge: used / capacity. */
export function Gauge({ label, used, capacity }: { label: string; used: number; capacity: number }) {
  const pct = capacity > 0 ? Math.min(100, (used / capacity) * 100) : 0;
  const hot = pct >= 85;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span className="mono" style={{ fontSize: 12, color: "var(--ink-muted)" }}>{label}</span>
        <span className="mono" style={{ fontSize: 12 }}>{used} / {capacity}</span>
      </div>
      <div style={{ height: 8, borderRadius: 999, background: "var(--paper-2)", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: hot ? "var(--signal)" : "var(--machine)" }} />
      </div>
    </div>
  );
}

/** Empty / error states: never a dead end — say what's true and what to do. */
export function EmptyState({ title, children, command }: { title: string; children?: ReactNode; command?: string }) {
  return (
    <Stack gap={10} align="center" style={{ padding: "44px 24px", textAlign: "center" }}>
      <Text fw={600} style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 16 }}>{title}</Text>
      {children && <Text size="sm" c="dimmed" maw={440}>{children}</Text>}
      {command && (
        <code className="mono" style={{ background: "var(--paper-2)", padding: "7px 11px", borderRadius: 6, fontSize: 13 }}>
          {command}
        </code>
      )}
    </Stack>
  );
}

/** Four-segment mini bar. */
export function Bars({ filled }: { filled: number }) {
  return (
    <span className="bars">
      {[0, 1, 2, 3].map((i) => <i key={i} className={i < filled ? "on" : ""} />)}
    </span>
  );
}

const num = (n: number) => (Number.isInteger(n) ? String(n) : String(n));

/** Turn a resource request into a human sense of cost + a 0–4 fill + scale word. */
export function computeCost(resources: Record<string, number>, capacity: Record<string, number>) {
  const keys = Object.keys(resources ?? {});
  if (!keys.length) return { text: "minimal", scale: "light", filled: 0 };
  let best = keys[0];
  let frac = 0;
  for (const k of keys) {
    const cap = capacity[k] ?? 0;
    const f = cap > 0 ? resources[k] / cap : 0;
    if (f >= frac) { frac = f; best = k; }
  }
  const cap = capacity[best] ?? 0;
  const text = cap > 0 ? `${num(resources[best])} of ${num(cap)} ${best}` : `${num(resources[best])} ${best}`;
  const scale = frac < 0.34 ? "light" : frac < 0.67 ? "moderate" : "heavy";
  const filled = Math.max(1, Math.min(4, Math.round(frac * 4)));
  return { text, scale, filled };
}

/** "12m" / "1h 5m" / "2d 3h" — a compact elapsed duration. */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60), mm = m % 60;
  if (h < 24) return mm ? `${h}h ${mm}m` : `${h}h`;
  const d = Math.floor(h / 24), hh = h % 24;
  return hh ? `${d}d ${hh}h` : `${d}d`;
}

/** "running 12m" tinted with the executing hue; just "running" if start unknown. */
export function Running({ since }: { since?: number | null }) {
  const label = since ? `running ${formatDuration(Date.now() / 1000 - since)}` : "running";
  return <span className="mono" style={{ fontSize: 12, color: "var(--st-executing)" }}>{label}</span>;
}

const _WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Turn a usage-skill reset like "Tue 7:19" into "Tue 1 Jul · 7:19" — the
 *  dashboard has the room for the date, unlike the statusline. */
export function formatReset(resets: string): string {
  const wd = resets.match(/\b(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\b/);
  const tm = resets.match(/(\d{1,2}:\d{2})/);
  if (!wd) return resets;
  const now = new Date();
  const days = (_WD.indexOf(wd[1]) - now.getDay() + 7) % 7;
  const d = new Date(now);
  d.setDate(now.getDate() + days);
  const date = d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return `${wd[1]} ${date}${tm ? " · " + tm[1] : ""}`;
}

/** One Claude-usage window bar (5-hour / weekly), tinted by pressure. */
export function UsageBar({ label, pct, resets }: { label: string; pct: number; resets: string }) {
  const color = pct >= 85 ? "var(--signal)" : pct >= 60 ? "#caa12a" : "var(--machine)";
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span className="mono" style={{ fontSize: 12, color: "var(--ink-muted)" }}>{label}</span>
        <span className="mono" style={{ fontSize: 12 }}>{pct}% · resets {formatReset(resets)}</span>
      </div>
      <div style={{ height: 8, borderRadius: 999, background: "var(--paper-2)", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${Math.min(100, pct)}%`, background: color }} />
      </div>
    </div>
  );
}

function RunStat({ label, agg }: { label: string; agg: RunAgg }) {
  return (
    <div style={{ flex: 1 }}>
      <Text size="xs" c="dimmed">{label} runs</Text>
      <Text style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 22, fontWeight: 600, lineHeight: 1.1 }}>{agg.total}</Text>
      <Text size="xs" c="dimmed">{agg.last_hour} in last hour</Text>
    </div>
  );
}

/** Claude usage: the rolling 5h/weekly budget plus PM and worker call counts. */
export function UsagePanel({ usage }: { usage: Usage }) {
  const w = usage.budget?.windows ?? {};
  return (
    <Stack gap={16}>
      {usage.budget ? (
        <Stack gap={10}>
          {w["5h"] && <UsageBar label="5-hour" pct={w["5h"].pct} resets={w["5h"].resets} />}
          {w["week"] && <UsageBar label="weekly" pct={w["week"].pct} resets={w["week"].resets} />}
          {!usage.budget.live && <Text size="xs" c="dimmed">showing last cached reading</Text>}
        </Stack>
      ) : <Text size="sm" c="dimmed">Usage reading unavailable.</Text>}
      <Group_ >
        <RunStat label="PM" agg={usage.runs.pm} />
        <RunStat label="Worker" agg={usage.runs.worker} />
      </Group_>
    </Stack>
  );
}

function Group_({ children }: { children: ReactNode }) {
  return <div style={{ display: "flex", gap: 18, borderTop: "1px solid var(--hairline)", paddingTop: 12 }}>{children}</div>;
}

/** Page heading with a mono eyebrow. */
export function PageHead({ eyebrow, title, right }: { eyebrow: string; title: string; right?: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 22 }}>
      <div>
        <div className="eyebrow" style={{ marginBottom: 4 }}>{eyebrow}</div>
        <h1 style={{ margin: 0, fontFamily: "'Space Grotesk', sans-serif", fontSize: 26, fontWeight: 600 }}>{title}</h1>
      </div>
      {right}
    </div>
  );
}
