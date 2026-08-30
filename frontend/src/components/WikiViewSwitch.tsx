import { Link } from "react-router-dom";

/**
 * Pages / Graph, as a switch rather than a link.
 *
 * The graph used to be reached by a `.view` link — bold teal with a `↗` —
 * sitting beside the "Wiki" title. That idiom means *departure*: it is what
 * "open wiki →" and "open full graph ↗" use, for going somewhere else. These
 * two are the same wiki looked at two ways, and dressing one of them as an
 * exit made it read as misplaced next to the page's own heading.
 *
 * Real `<Link>`s, not a segmented control: these are navigation, so they
 * should carry an href, show it in the status bar and open in a tab on a
 * middle click. `aria-current` is what marks the one you are on — it drives
 * both the styling and the accessible answer to "where am I".
 */
export default function WikiViewSwitch(
  { programId, current }: { programId: string; current: "pages" | "graph" },
) {
  return (
    <nav className="viewswitch" aria-label="wiki view">
      <Link to={`/programs/${programId}/wiki`}
            aria-current={current === "pages" ? "page" : undefined}>
        Pages
      </Link>
      <Link to={`/programs/${programId}/wiki/graph`}
            aria-current={current === "graph" ? "page" : undefined}>
        Graph
      </Link>
    </nav>
  );
}
