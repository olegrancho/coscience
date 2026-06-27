import type { ReactNode } from "react";
import { AppShell, Group, Text } from "@mantine/core";
import { NavLink, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { Heartbeat } from "./components/ui";
import Overview from "./views/Overview";
import Programs from "./views/ProgramsOverview";
import ProgramDetail from "./views/ProgramDetail";
import SprintDetail from "./views/SprintDetail";
import ResultDetail from "./views/ResultDetail";
import Ledger from "./views/Ledger";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/programs", label: "Programs", end: false },
  { to: "/ledger", label: "Ledger", end: false },
];

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
    </div>
  );
}

export default function App() {
  return (
    <AppShell header={{ height: 52 }} navbar={{ width: 232, breakpoint: "sm" }} padding={0}>
      <AppShell.Header style={{ background: "var(--card)", borderBottom: "1px solid var(--hairline)" }}>
        <Group h="100%" px="lg" justify="flex-end">
          <Group gap={7}>
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
            <NavLink key={n.to} to={n.to} end={n.end} style={railLinkStyle}>
              {n.label}
            </NavLink>
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
              <Route path="/sprints/:id" element={<SprintDetail />} />
              <Route path="/results/:id" element={<ResultDetail />} />
              <Route path="/ledger" element={<Ledger />} />
            </Routes>
          </div>
        </div>
      </AppShell.Main>
    </AppShell>
  );
}
