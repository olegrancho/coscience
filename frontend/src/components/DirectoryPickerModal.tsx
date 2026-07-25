import { ActionIcon, Button, Group, Modal, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";

interface Props {
  opened: boolean;
  initialPath?: string;
  onClose: () => void;
  onPick: (path: string) => void;
}

/** Browse the SERVER's filesystem to choose a folder. Confined server-side to
 *  the configured roots; `null` path means "the root level". */
export default function DirectoryPickerModal({ opened, initialPath, onClose, onPick }: Props) {
  const qc = useQueryClient();
  const [path, setPath] = useState<string | null>(initialPath?.trim() || null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (opened) { setPath(initialPath?.trim() || null); setCreating(false); setError(""); }
  }, [opened, initialPath]);

  const { data, error: qErr } = useQuery({
    queryKey: ["fs-dirs", path],
    queryFn: () => api.listDirs(path),
    enabled: opened,
    retry: false,
  });

  // A saved-but-unusable initialPath (typo, deleted, outside the roots) drops
  // the user at the root level instead of a dead modal.
  useEffect(() => { if (qErr && path !== null) setPath(null); }, [qErr, path]);

  const go = (p: string | null) => { setError(""); setCreating(false); setNewName(""); setPath(p); };

  const root = data?.roots.find((r) => data.path === r.path || data.path?.startsWith(`${r.path}/`));
  const rest = root && data?.path ? data.path.slice(root.path.length).split("/").filter(Boolean) : [];
  const canUp = !!data && (data.parent !== null || (data.path !== null && data.roots.length > 1));

  const create = async () => {
    setError("");
    if (!data?.path) { setError("Choose a folder first."); return; }
    try {
      await api.createDir(data.path, newName.trim());
      setCreating(false);
      setNewName("");
      qc.invalidateQueries({ queryKey: ["fs-dirs", path] });
    } catch (e) { setError(String(e)); }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Choose project folder" size="lg">
      <Stack gap="sm">
        <Group gap={4} wrap="wrap" align="center">
          {data && data.roots.length > 1 && (
            <button type="button" className="linklike" onClick={() => go(null)}>roots</button>
          )}
          {root && <button type="button" className="linklike" onClick={() => go(root.path)}>{root.label}</button>}
          {rest.map((seg, i) => (
            <span key={`${seg}-${i}`}>
              <span style={{ color: "var(--ink-faint)" }}> / </span>
              <button type="button" className="linklike"
                      onClick={() => go(`${root!.path}/${rest.slice(0, i + 1).join("/")}`)}>{seg}</button>
            </span>
          ))}
          <ActionIcon variant="subtle" size="sm" aria-label="up one folder"
                      disabled={!canUp} onClick={() => go(data?.parent ?? null)}>↑</ActionIcon>
        </Group>

        <ScrollArea.Autosize mah={320} type="auto">
          <Stack gap={2}>
            {data?.entries.length === 0 && <Text size="sm" c="dimmed">No subfolders here.</Text>}
            {data?.entries.map((e) => (
              <button key={e.path} type="button" className="linklike" style={{ textAlign: "left" }}
                      onClick={() => go(e.path)}>📁 {e.name}</button>
            ))}
          </Stack>
        </ScrollArea.Autosize>

        {creating ? (
          <Group gap={6}>
            <TextInput size="xs" aria-label="new folder name" placeholder="new folder name"
                       value={newName} autoFocus
                       onChange={(ev) => setNewName(ev.currentTarget.value)}
                       onKeyDown={(ev) => { if (ev.key === "Enter") void create(); }} />
            <Button size="xs" onClick={create}>Create</Button>
            <Button size="xs" variant="default"
                    onClick={() => { setCreating(false); setNewName(""); }}>Cancel</Button>
          </Group>
        ) : (
          <button type="button" className="linklike" style={{ textAlign: "left" }}
                  disabled={!data?.path} onClick={() => setCreating(true)}>＋ New folder</button>
        )}

        {error && <Text size="sm" c="red">{error}</Text>}

        <Text size="xs" className="mono" c="dimmed">{data?.path ?? "choose a root"}</Text>

        <Group justify="flex-end" gap={8}>
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button disabled={!data?.path}
                  onClick={() => { if (data?.path) { onPick(data.path); onClose(); } }}>
            Use this folder
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
