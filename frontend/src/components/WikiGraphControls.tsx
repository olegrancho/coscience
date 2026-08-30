// The concept graph's chrome: what is shown, and what the picture means.
//
// Both were bare browser defaults before — `<fieldset><legend>` with loose
// checkboxes for the filters, and nothing at all for the encoding, so the
// colours were a private language. They live here rather than in the view so
// WikiGraphView stays about the graph.
import { Chip, Group, Popover, Text } from "@mantine/core";
import { TYPE_HUE, TENSION_COLOUR } from "./wikiGraphStyle";
import { isAll, type FilterOptions, type ResolvedFilters } from "./wikiGraphFilter";

type GroupKey = "types" | "relations" | "trust";

const GROUP_LABEL: Record<GroupKey, string> = {
  types: "type", relations: "relation", trust: "trust",
};

function FilterGroup(
  { name, options, shown, onToggle, onAll }: {
    name: GroupKey; options: string[]; shown: Set<string>;
    onToggle: (v: string) => void; onAll: () => void;
  },
) {
  if (options.length === 0) return null;
  const complete = isAll(shown, options);
  return (
    <div className="graph-filter-group">
      {/* The label doubles as the way back to "everything". A group narrowed
          down otherwise has no single click that undoes it, and the reader is
          left ticking boxes to guess which ones they started from. */}
      <button type="button" className="graph-filter-label"
              onClick={onAll} disabled={complete}
              aria-label={`show all ${GROUP_LABEL[name]}`}
              title={complete ? "showing all" : "show all again"}>
        {GROUP_LABEL[name]}
        {!complete && <span aria-hidden="true"> ↺</span>}
      </button>
      <Group gap={4} wrap="wrap">
        {options.map((v) => (
          <Chip key={v} size="xs" radius="sm" variant="outline" color="machine"
                checked={shown.has(v)} onChange={() => onToggle(v)}>
            {v}
          </Chip>
        ))}
      </Group>
    </div>
  );
}

export function GraphFilters(
  { options, shown, onToggle, onAll, typedOnly, onTypedOnly, tension, onTension, onReset }: {
    options: FilterOptions;
    shown: ResolvedFilters;
    onToggle: (g: GroupKey, v: string) => void;
    onAll: (g: GroupKey) => void;
    typedOnly: boolean;
    onTypedOnly: (v: boolean) => void;
    tension: boolean;
    onTension: (v: boolean) => void;
    onReset: () => void;
  },
) {
  return (
    <div className="graph-toolbar">
      <FilterGroup name="types" options={options.types} shown={shown.types}
                   onToggle={(v) => onToggle("types", v)} onAll={() => onAll("types")} />
      <FilterGroup name="relations" options={options.relations} shown={shown.relations}
                   onToggle={(v) => onToggle("relations", v)} onAll={() => onAll("relations")} />
      <FilterGroup name="trust" options={options.trust} shown={shown.trust}
                   onToggle={(v) => onToggle("trust", v)} onAll={() => onAll("trust")} />

      <div className="graph-toolbar-tail">
        <Chip size="xs" radius="sm" variant="outline" color="machine"
              checked={typedOnly} onChange={onTypedOnly}>
          typed only
        </Chip>
        <Chip size="xs" radius="sm" variant="outline" color="signal"
              checked={tension} onChange={onTension}>
          tension
        </Chip>
        <button type="button" className="linklike" onClick={onReset}
                title="forget every node you have dragged and let the simulation settle">
          reset layout
        </button>
        <GraphLegend />
      </div>
    </div>
  );
}

function Swatch({ fill, border, outline, children }: {
  fill?: string; border: string; outline?: string; children: React.ReactNode;
}) {
  return (
    <Group gap={7} wrap="nowrap">
      <span style={{
        width: 12, height: 12, borderRadius: "50%", flex: "0 0 auto",
        background: fill ?? "transparent", border: `2px solid ${border}`,
        outline, outlineOffset: 1,
      }} />
      <Text size="xs">{children}</Text>
    </Group>
  );
}

function Stroke({ dash, colour, heads, children }: {
  dash?: string; colour: string; heads: 1 | 2; children: React.ReactNode;
}) {
  return (
    <Group gap={7} wrap="nowrap">
      <svg width={26} height={12} style={{ flex: "0 0 auto" }} aria-hidden="true">
        <defs>
          <marker id={`lg-${colour.replace(/[^a-z0-9]/gi, "")}`} markerWidth={5} markerHeight={5}
                  refX={4} refY={2.5} orient="auto">
            <path d="M0,0 L5,2.5 L0,5 z" fill={colour} />
          </marker>
        </defs>
        <line x1={heads === 2 ? 5 : 1} y1={6} x2={21} y2={6} stroke={colour} strokeWidth={1.5}
              strokeDasharray={dash}
              markerEnd={`url(#lg-${colour.replace(/[^a-z0-9]/gi, "")})`} />
      </svg>
      <Text size="xs">{children}</Text>
    </Group>
  );
}

/** Deliberately behind a click. A key that is always on screen costs the graph
 *  the space it explains, and it is read once and then never again. */
export function GraphLegend() {
  return (
    <Popover width={280} position="bottom-end" withArrow shadow="md">
      <Popover.Target>
        <button type="button" className="linklike" aria-label="legend">legend</button>
      </Popover.Target>
      <Popover.Dropdown>
        <div className="graph-legend">
          <div className="eyebrow">colour — page type</div>
          <Swatch border={TYPE_HUE.Concept}>Concept</Swatch>
          <Swatch border={TYPE_HUE.Entity}>Entity</Swatch>
          <Swatch border={TYPE_HUE.Synthesis}>Synthesis</Swatch>

          <div className="eyebrow">fill — trust</div>
          <Swatch border="var(--hairline-strong)">unverified — nothing has checked it</Swatch>
          <Swatch border="var(--hairline-strong)" fill="var(--ink-faint)">
            machine-confirmed
          </Swatch>
          <Swatch border="var(--machine)" fill="var(--machine)">human-reviewed</Swatch>

          <div className="eyebrow">the node itself</div>
          <Swatch border="var(--hairline-strong)" outline="2px dotted #8a8f98">
            orphan — nothing links to or from it
          </Swatch>
          <Text size="xs">Size grows with how many relations a page has.</Text>
          <Text size="xs">A <s>struck-through</s> name is a deprecated page.</Text>

          <div className="eyebrow">lines</div>
          <Stroke colour="#8a8f98" heads={1}>a declared relation, pointing at its target</Stroke>
          <Stroke colour="#8a8f98" heads={1} dash="3 3">a plain link in the page body</Stroke>
          <Stroke colour={TENSION_COLOUR.contradicts} heads={2}>
            contradicts — drawn once, with a head at each end
          </Stroke>

          <div className="eyebrow">tension lens</div>
          <Text size="xs">
            Paints disagreement and dims everything else:{" "}
            <b style={{ color: TENSION_COLOUR.contradicts }}>contradicts</b>,{" "}
            <b style={{ color: TENSION_COLOUR.replaces }}>replaces</b>,{" "}
            <b style={{ color: TENSION_COLOUR.refines }}>refines</b>.
          </Text>
        </div>
      </Popover.Dropdown>
    </Popover>
  );
}
