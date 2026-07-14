import { useState } from "react";
import { Button, Select, Stack, Text, Tooltip } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CurrentUser } from "./api";

export function useMe() {
  // /api/me is a soft 200 endpoint; no retry/poll — auth state changes only on
  // login/logout, which invalidate this query explicitly.
  return useQuery({ queryKey: ["me"], queryFn: api.me, retry: false, refetchInterval: false });
}

/** Initials avatar + name for an attributed action. Resolves display from the
 *  registry; falls back to the raw username, or "—" when unattributed. */
export function UserChip({ username }: { username?: string }) {
  const users = useQuery({ queryKey: ["users"], queryFn: api.listUsers });
  if (!username) return <Text component="span" size="xs" c="dimmed">—</Text>;
  const u = (users.data ?? []).find((x) => x.username === username);
  const initials = u?.initials ?? username.slice(0, 2).toUpperCase();
  const name = u?.name ?? username;
  return (
    <Tooltip label={name}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
        <span style={{ width: 18, height: 18, borderRadius: 9, fontSize: 9, fontWeight: 700,
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          background: "var(--machine-weak)", color: "var(--machine)" }}>{initials}</span>
        <Text component="span" size="xs" c="dimmed">{name}</Text>
      </span>
    </Tooltip>
  );
}

function Login() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: api.listUsers });
  const [who, setWho] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const submit = async () => {
    if (!who) return;
    try { await api.login(who); qc.invalidateQueries(); }
    catch { setErr("Login failed"); }
  };
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
      <Stack gap="sm" style={{ width: 320 }}>
        <Text fw={700} size="lg">Sign in</Text>
        <Select label="Who are you?" placeholder="Pick your name" searchable
          data={(users.data ?? []).map((u) => ({ value: u.username, label: u.name }))}
          value={who} onChange={setWho} />
        {err && <Text size="xs" c="red">{err}</Text>}
        <Button onClick={submit} disabled={!who}>Enter</Button>
      </Stack>
    </div>
  );
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const me = useMe();
  if (me.isLoading) return null;
  // /api/me always 200: seeded + logged-out => {required:true, user:null} => Login.
  if (me.data?.required && !me.data.user) return <Login />;
  return <>{children}</>;
}
