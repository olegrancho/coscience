import { MultiSelect, Switch, Stack } from "@mantine/core";
import type { Access } from "./programAccess";

interface Props {
  value: Access;
  onChange: (a: Access) => void;
  programs: { id: string; title?: string }[];
}

/** A server's program access: "All programs" (with optional exceptions) or
 *  "Only these programs". Used from the server dialog, in add, edit and local mode. */
export default function ProgramAccessInput({ value, onChange, programs }: Props) {
  const known = new Set(programs.map((p) => p.id));
  const data = [
    ...programs.map((p) => ({ value: p.id, label: p.title ? `${p.title} (${p.id})` : p.id })),
    ...value.list.filter((id) => !known.has(id)).map((id) => ({ value: id, label: id })),
  ];

  return (
    <Stack gap={4}>
      <Switch
        label="All programs"
        checked={value.all}
        onChange={(e) => onChange({ all: e.currentTarget.checked, list: [] })}
      />
      <MultiSelect
        label={value.all ? "Except" : "Only these programs"}
        placeholder={value.all ? "no exceptions" : undefined}
        data={data}
        value={value.list}
        onChange={(list) => onChange({ ...value, list })}
        searchable
        clearable
        error={!value.all && value.list.length === 0
          ? "Pick at least one program, or turn on All programs" : undefined}
      />
    </Stack>
  );
}
