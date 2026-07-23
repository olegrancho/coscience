import type { ReactNode } from "react";
import { AppShell, Group, Text } from "@mantine/core";
import { Link, Route, Routes, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { useMe, UserChip } from "./auth";
import { Heartbeat } from "./components/ui";
import Overview from "./views/Overview";
import Programs from "./views/ProgramsOverview";
import ProgramDetail from "./views/ProgramDetail";
import IdeasView from "./views/IdeasView";
import ChatView from "./views/ChatView";
import SprintDetail from "./views/SprintDetail";
import ResultDetail from "./views/ResultDetail";
import ArtifactDetail from "./views/ArtifactDetail";
import Ledger from "./views/Ledger";

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/programs", label: "Programs" },
  { to: "/ledger", label: "Compute" },
];

/** Which rail item owns the current route. Experiment + result detail pages live
 *  under the Programs section so the rail stays highlighted while you drill in. */
function activeSection(pathname: string): string {
  if (pathname === "/") return "/";
  if (pathname.startsWith("/ledger")) return "/ledger";
  if (pathname.startsWith("/programs") || pathname.startsWith("/sprints") || pathname.startsWith("/results"))
    return "/programs";
  return "";
}

function railLinkStyle({ isActive }: { isActive: boolean }) {
  return {
    display: "block",
    padding: "9px 12px",
    borderRadius: 8,
    marginBottom: 2,
    fontWeight: 500,
    fontSize: 14,
    textDecoration: "none",
    color: isActive ? "var(--ink)" : "var(--ink-muted)",
    background: isActive ? "var(--machine-weak)" : "transparent",
    boxShadow: isActive ? "inset 2px 0 0 var(--machine)" : "none",
  };
}

function Pulse() {
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.listPrograms });
  const sprints = useQuery({ queryKey: ["sprints"], queryFn: api.listSprints });
  const st: Record<string, string> = {};
  for (const p of programs.data ?? []) st[p.id] = p.status;
  const progOf = (s: { id: string; program: string | null }) =>
    s.program ?? (s.id.includes("-") ? s.id.slice(0, s.id.indexOf("-")) : s.id);
  const active = (programs.data ?? []).filter((p) => p.status === "active").length;
  const running = (sprints.data ?? []).filter((s) => s.status === "executing").length;
  const waiting = (sprints.data ?? [])
    .filter((s) => s.status === "proposed" && (st[progOf(s)] ?? "active") === "active").length;

  const Row = ({ children }: { children: ReactNode }) => (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--ink-muted)" }}>
      {children}
    </div>
  );

  return (
    <div style={{ borderTop: "1px solid var(--hairline)", paddingTop: 14, display: "grid", gap: 9 }}>
      <div className="eyebrow">pulse</div>
      <Row>
        {active > 0 ? <Heartbeat /> : <span style={{ width: 9, height: 9, borderRadius: 9, background: "var(--ink-faint)" }} />}
        <span><b className="mono" style={{ color: "var(--ink)" }}>{active}</b> active {active === 1 ? "program" : "programs"}</span>
      </Row>
      <Row>
        <span style={{ width: 9, textAlign: "center", color: "var(--st-executing)" }}>▶</span>
        <span><b className="mono" style={{ color: "var(--ink)" }}>{running}</b> running</span>
      </Row>
      <Row>
        <span style={{ width: 9, textAlign: "center", color: waiting ? "var(--signal)" : "var(--ink-faint)" }}>●</span>
        <span style={{ color: waiting ? "var(--signal)" : "var(--ink-muted)", fontWeight: waiting ? 600 : 400 }}>
          <b className="mono">{waiting}</b> awaiting you
        </span>
      </Row>
      <UsageBars />
    </div>
  );
}

/** Claude budget as horizontal fill bars in the rail's pulse: how much of the
 *  rolling 5-hour window and the weekly allowance is spent, with the reset time.
 *  Turns amber past 85% so the "you're about to get throttled" moment is visible
 *  before agents start pausing. */
function UsageBars() {
  const usage = useQuery({ queryKey: ["usage"], queryFn: api.getUsage, refetchInterval: 30_000 });
  const windows = usage.data?.budget?.windows;
  if (!windows) return null;
  const rows: { key: string; label: string }[] = [
    { key: "5h", label: "5h window" },
    { key: "week", label: "this week" },
  ];
  const shown = rows.filter((r) => windows[r.key]);
  if (!shown.length) return null;
  return (
    <div style={{ display: "grid", gap: 9, marginTop: 4 }}>
      {shown.map(({ key, label }) => {
        const w = windows[key];
        const pct = Math.max(0, Math.min(100, Math.round(w.pct)));
        const hot = pct >= 85;
        return (
          <div key={key} style={{ display: "grid", gap: 3 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--ink-muted)" }}>
              <span>{label}</span>
              <span className="mono" style={{ color: hot ? "var(--signal)" : "var(--ink)" }}>{pct}%</span>
            </div>
            <div style={{ height: 6, borderRadius: 6, background: "var(--hairline)", overflow: "hidden" }}>
              <div style={{ width: `${pct}%`, height: "100%", borderRadius: 6,
                background: hot ? "var(--signal)" : "var(--machine)", transition: "width .3s ease" }} />
            </div>
            <div style={{ fontSize: 10, color: "var(--ink-faint)" }}>resets {w.resets}</div>
          </div>
        );
      })}
    </div>
  );
}

/** Warns when the API server is running a different build than this page —
 *  the classic "server wasn't restarted after a rebuild" trap, which surfaces
 *  as blank pages / 500s when old code meets new on-disk data. Polls so a
 *  mid-session restart clears (or raises) the warning on its own. */
function VersionBanner() {
  const v = useQuery({
    queryKey: ["version"],
    queryFn: api.getVersion,
    refetchInterval: 30_000,
  });
  const server = v.data?.sha;
  const page = __APP_VERSION__;
  const drift = !!server && server !== "unknown" && page !== "unknown" && server !== page;
  if (!drift) return null;
  return (
    <Group gap={7} style={{ marginRight: "auto" }} wrap="nowrap">
      <span style={{ color: "var(--signal)", fontSize: 13 }}>⚠</span>
      <Text size="xs" c="dimmed">
        server <span className="mono" style={{ color: "var(--ink)" }}>{server}</span> ≠ page{" "}
        <span className="mono" style={{ color: "var(--ink)" }}>{page}</span> — restart the backend, then{" "}
        <button type="button" className="linklike" onClick={() => location.reload()}>reload</button>
      </Text>
    </Group>
  );
}

function UserMenu() {
  const me = useMe();
  const qc = useQueryClient();
  if (!me.data?.user) return null;
  return (
    <Group gap={10} wrap="nowrap">
      <UserChip username={me.data.user.username} />
      <button type="button" className="linklike"
        onClick={async () => { await api.logout(); qc.invalidateQueries(); }}>log out</button>
    </Group>
  );
}

export default function App() {
  const { pathname } = useLocation();
  const active = activeSection(pathname);
  return (
    <AppShell header={{ height: 52 }} navbar={{ width: 232, breakpoint: "sm" }} padding={0}>
      <AppShell.Header style={{ background: "var(--card)", borderBottom: "1px solid var(--hairline)" }}>
        <Group h="100%" px="lg" justify="flex-end" wrap="nowrap">
          <VersionBanner />
          <UserMenu />
          <Group gap={7} wrap="nowrap">
            <span className="heartbeat" />
            <Text className="mono" size="xs" c="dimmed">live · refreshes every 10s</Text>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar
        p="md"
        style={{ background: "var(--card)", borderRight: "1px solid var(--hairline)", display: "flex", flexDirection: "column" }}
      >
        <div style={{ marginBottom: 22 }}>
          <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700, fontSize: 17, letterSpacing: "-0.01em" }}>
            <span style={{ color: "var(--machine)" }}>◇</span> Co<span style={{ color: "var(--ink-faint)" }}>·</span>Science
          </div>
          <div className="eyebrow" style={{ marginTop: 3 }}>research oversight</div>
        </div>

        <nav style={{ flex: 1 }}>
          {NAV.map((n) => (
            <Link key={n.to} to={n.to} style={railLinkStyle({ isActive: active === n.to })}>
              {n.label}
            </Link>
          ))}
        </nav>

        <Pulse />
      </AppShell.Navbar>

      <AppShell.Main>
        <div className="app-canvas" style={{ minHeight: "calc(100vh - 52px)", padding: "26px 30px" }}>
          <div style={{ maxWidth: 980, margin: "0 auto" }}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/programs" element={<Programs />} />
              <Route path="/programs/:id" element={<ProgramDetail />} />
              <Route path="/programs/:id/ideas" element={<IdeasView />} />
              <Route path="/programs/:id/chat" element={<ChatView />} />
              <Route path="/sprints/:id" element={<SprintDetail />} />
              <Route path="/results/:id" element={<ResultDetail />} />
              <Route path="/programs/:id/artifacts/:aid" element={<ArtifactDetail />} />
              <Route path="/ledger" element={<Ledger />} />
            </Routes>
          </div>
        </div>
      </AppShell.Main>
    </AppShell>
  );
}
