import { Group, MultiSelect } from "@mantine/core";

interface Props {
  value: string[];
  onChange: (v: string[]) => void;
  programs: { id: string; title?: string }[];
}

/** A server's program list: one plain checklist, picked from a dropdown.
 *  Used from the server dialog, in add, edit and local mode. */
export default function ProgramAccessInput({ value, onChange, programs }: Props) {
  const known = new Set(programs.map((p) => p.id));
  const data = [
    ...programs.map((p) => ({ value: p.id, label: p.title ? `${p.title} (${p.id})` : p.id })),
    ...value.filter((id) => !known.has(id)).map((id) => ({ value: id, label: id })),
  ];

  return (
    <MultiSelect
      label={
        <Group gap={8} wrap="nowrap" component="span">
          <span>Programs this server runs</span>
          <button type="button" className="linklike" onClick={() => onChange(data.map((d) => d.value))}>
            select all
          </button>
          <button type="button" className="linklike" onClick={() => onChange([])}>
            clear
          </button>
        </Group>
      }
      description="Empty means this server takes no work."
      data={data}
      value={value}
      onChange={onChange}
      searchable
      clearable
    />
  );
}
