import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface TocEntry { id: string; label: string }

export default function PageToc({ entries }: { entries: TocEntry[] }) {
  const [active, setActive] = useState<string>("");
  const observerRef = useRef<IntersectionObserver | null>(null);
  const entryIds = entries.map((e) => e.id).join(",");

  useEffect(() => {
    observerRef.current?.disconnect();
    // Scroll-spy is an enhancement, not the feature. jsdom has no
    // IntersectionObserver, and constructing one where it is missing throws
    // during commit — which takes the whole page down, not just the nav.
    if (typeof IntersectionObserver === "undefined") return;

    const ids = entryIds.split(",").filter(Boolean);
    const visibleRatios = new Map<string, number>();

    observerRef.current = new IntersectionObserver(
      (records) => {
        for (const r of records) visibleRatios.set(r.target.id, r.intersectionRatio);
        for (const id of ids) {
          if ((visibleRatios.get(id) ?? 0) > 0) { setActive(id); return; }
        }
      },
      { threshold: [0, 0.1, 0.5, 1], rootMargin: "-60px 0px -40% 0px" },
    );

    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) observerRef.current.observe(el);
    }

    return () => observerRef.current?.disconnect();
  }, [entryIds]);

  if (entries.length < 2) return null;

  return createPortal(
    <nav className="page-toc" aria-label="Page sections">
      {entries.map((e) => (
        <a
          key={e.id}
          href={`#${e.id}`}
          className={e.id === active ? "active" : undefined}
          onClick={(ev) => {
            ev.preventDefault();
            setActive(e.id);
            document.getElementById(e.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}
        >
          {e.label}
        </a>
      ))}
    </nav>,
    document.body,
  );
}
