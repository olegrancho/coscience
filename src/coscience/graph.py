"""Research lineage graph: typed edges between idea / experiment / result nodes.

Pure logic — no IO. Each edge is stored on its SOURCE node (outbound). This
module owns the vocabulary, node-stage rules, edge minting, the reverse index,
validation, cycle detection, and rewiring. Every substrate write happens in
pm_agent.py / service.py, keeping writes out of the reasoner (the seam rule)."""
from __future__ import annotations

import hashlib

from coscience.models import Idea, Sprint, SprintStatus

# families
LINEAGE = "lineage"
EVIDENTIAL = "evidential"

# node stages / kinds
IDEA = "idea"
EXPERIMENT = "experiment"
RESULT = "result"

# The frozen edge vocabulary. `src`/`dst` are kind slots: "idea", "experiment",
# or "any". `same_kind` forces src and dst to the same kind. `require_done` gates
# evidential edges to result-stage (done) endpoints.
EDGE_SPEC: dict[str, dict] = {
    "inspired_by":  {"family": LINEAGE,    "tier": "core",     "src": "any",       "dst": "any"},
    "builds_on":    {"family": LINEAGE,    "tier": "core",     "src": EXPERIMENT,  "dst": EXPERIMENT},
    "supersedes":   {"family": LINEAGE,    "tier": "core",     "src": EXPERIMENT,  "dst": EXPERIMENT},
    "confirms":     {"family": EVIDENTIAL, "tier": "core",     "src": EXPERIMENT,  "dst": EXPERIMENT, "require_done": True},
    "refutes":      {"family": EVIDENTIAL, "tier": "core",     "src": EXPERIMENT,  "dst": EXPERIMENT, "require_done": True},
    "refines":      {"family": LINEAGE,    "tier": "extended", "src": IDEA,        "dst": IDEA},
    "follows":      {"family": LINEAGE,    "tier": "extended", "src": "any",       "dst": "any", "same_kind": True},
    "replicates":   {"family": LINEAGE,    "tier": "extended", "src": EXPERIMENT,  "dst": EXPERIMENT},
    "duplicate_of": {"family": LINEAGE,    "tier": "extended", "src": "any",       "dst": "any", "same_kind": True},
    "contradicts":  {"family": EVIDENTIAL, "tier": "extended", "src": EXPERIMENT,  "dst": EXPERIMENT, "require_done": True},
}

CORE_TYPES = {t for t, s in EDGE_SPEC.items() if s["tier"] == "core"}
EXTENDED_TYPES = {t for t, s in EDGE_SPEC.items() if s["tier"] == "extended"}
# Ship Core first. Enabling Extended later is a one-line change: ENABLED_TYPES |= EXTENDED_TYPES
ENABLED_TYPES = set(CORE_TYPES)


def node_kind(node: "Idea | Sprint") -> str:
    return IDEA if isinstance(node, Idea) else EXPERIMENT


def node_stage(node: "Idea | Sprint") -> str:
    if isinstance(node, Idea):
        return IDEA
    return RESULT if node.status == SprintStatus.DONE else EXPERIMENT


def edge_id(etype: str, src: str, dst: str) -> str:
    return hashlib.sha1(f"{etype}|{src}|{dst}".encode("utf-8")).hexdigest()[:12]


def new_edge(etype: str, src: str, dst: str, source: str, by: str = "",
             at: float = 0.0, rationale: str = "", confidence: str = "",
             evidence: str = "") -> dict:
    return {"id": edge_id(etype, src, dst), "type": etype, "src": src, "dst": dst,
            "source": source, "by": by, "at": at, "rationale": rationale,
            "confidence": confidence, "evidence": evidence}


def all_edges(nodes) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        out.extend(n.edges)
    return out


def build_reverse_index(edges) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for e in edges:
        idx.setdefault(e["dst"], []).append(e)
    return idx


def repoint_edges(old_id: str, new_id: str, nodes) -> set[str]:
    """Move old_id's outbound edges onto new_id and rewrite every edge that points
    AT old_id to point at new_id. Both `old` and `new` must be present in `nodes`
    for the outbound move; inbound rewrites scan all nodes. Edge ids are refreshed
    (they are a hash of type|src|dst). Any edge that would collapse into a self-loop
    (src == dst) after repointing is dropped. Returns changed node ids (excludes
    old_id)."""
    by_id = {n.id: n for n in nodes}
    changed: set[str] = set()
    old = by_id.get(old_id)
    new = by_id.get(new_id)
    # Detach old's outbound edges first, so the inbound rewrite below cannot see
    # (and double-process) an edge we are about to move onto `new`.
    moved: list[dict] = []
    if old is not None and new is not None and old.edges:
        moved = old.edges
        old.edges = []
    # Rewrite inbound edges (dst == old_id) across all remaining node edge lists.
    for n in nodes:
        kept: list[dict] = []
        touched = False
        for e in n.edges:
            if e["dst"] == old_id:
                touched = True
                e["dst"] = new_id
                if e["src"] == e["dst"]:
                    continue                    # would-be self-loop -> drop
                e["id"] = edge_id(e["type"], e["src"], e["dst"])
            kept.append(e)
        if touched:
            n.edges = kept
            changed.add(n.id)
    # Attach old's moved outbound edges to new, repointing src (drop self-loops).
    # Dedup by id so a re-run (e.g. a killed-then-resumed PM cycle replaying the
    # same promotion) cannot append a transferred edge twice.
    if new is not None:
        new_ids = {e["id"] for e in new.edges}
        for e in moved:
            e["src"] = new_id
            if e["dst"] == old_id:              # old had a self-loop-shaped edge
                e["dst"] = new_id
            if e["src"] == e["dst"]:
                continue                        # never create a self-loop
            e["id"] = edge_id(e["type"], e["src"], e["dst"])
            if e["id"] in new_ids:
                continue                        # already transferred (idempotent re-run)
            new.edges.append(e)
            new_ids.add(e["id"])
            changed.add(new_id)
    return changed


def drop_evidential_incident(node_id: str, nodes) -> set[str]:
    """Remove every evidential edge touching node_id (either direction). Used on
    demote: the node becomes an idea, which has no result to confirm/refute."""
    changed: set[str] = set()
    for n in nodes:
        kept = [e for e in n.edges
                if not (EDGE_SPEC.get(e["type"], {}).get("family") == EVIDENTIAL
                        and (e["src"] == node_id or e["dst"] == node_id))]
        if len(kept) != len(n.edges):
            n.edges = kept
            changed.add(n.id)
    return changed


def drop_edges_to(dst_id: str, nodes) -> set[str]:
    """Drop every edge pointing AT dst_id (inbound). Used when a node is deleted
    outright (idea prune / delete) rather than transitioned, so no surviving node
    is left holding a dangling edge. Returns changed node ids."""
    changed: set[str] = set()
    for n in nodes:
        kept = [e for e in n.edges if e["dst"] != dst_id]
        if len(kept) != len(n.edges):
            n.edges = kept
            changed.add(n.id)
    return changed


def _kind_ok(kind: str, slot: str) -> bool:
    return slot == "any" or kind == slot


def drop_kind_illegal_incident(node_id: str, nodes) -> set[str]:
    """Drop edges incident on node_id whose kind pair is no longer legal — e.g. an
    experiment->experiment lineage edge repointed onto an idea during demotion.
    Implements the spec's "repoint … where still valid" clause. Returns changed
    node ids."""
    by_id = {n.id: n for n in nodes}
    changed: set[str] = set()
    for n in nodes:
        kept: list[dict] = []
        for e in n.edges:
            spec = EDGE_SPEC.get(e["type"])
            if spec and (e["src"] == node_id or e["dst"] == node_id):
                s, d = by_id.get(e["src"]), by_id.get(e["dst"])
                if (s is not None and d is not None
                        and (not _kind_ok(node_kind(s), spec["src"])
                             or not _kind_ok(node_kind(d), spec["dst"]))):
                    changed.add(n.id)
                    continue                    # drop the now-illegal edge
            kept.append(e)
        if len(kept) != len(n.edges):
            n.edges = kept
    return changed


def would_create_cycle(src: str, dst: str, lineage_edges) -> bool:
    """Adding src->dst closes a cycle iff dst can already reach src via existing
    lineage edges (each edge treated as directed src->dst)."""
    adj: dict[str, set[str]] = {}
    for e in lineage_edges:
        adj.setdefault(e["src"], set()).add(e["dst"])
    seen: set[str] = set()
    stack = [dst]
    while stack:
        u = stack.pop()
        if u == src:
            return True
        if u in seen:
            continue
        seen.add(u)
        stack.extend(adj.get(u, ()))
    return False


def validate_edge(edge, nodes, existing_edges, enabled=None) -> str | None:
    """Return a human-readable reason the edge is invalid, or None if it is valid.
    `nodes` are the live node objects; `existing_edges` is the current edge set
    (used for the DAG check on lineage edges)."""
    enabled = ENABLED_TYPES if enabled is None else enabled
    et = edge["type"]
    if et not in enabled:
        return f"type not enabled: {et}"
    src, dst = edge["src"], edge["dst"]
    if src == dst:
        return "self-edge"
    by_id = {n.id: n for n in nodes}
    if src not in by_id or dst not in by_id:
        return "endpoint missing"
    spec = EDGE_SPEC[et]
    ks, kd = node_kind(by_id[src]), node_kind(by_id[dst])
    if not _kind_ok(ks, spec["src"]) or not _kind_ok(kd, spec["dst"]):
        return "illegal kind pair"
    if spec.get("same_kind") and ks != kd:
        return "must be same kind"
    if spec.get("require_done"):
        if node_stage(by_id[src]) != RESULT or node_stage(by_id[dst]) != RESULT:
            return "evidential edge requires done endpoints"
        if edge.get("confidence") not in ("low", "med", "high"):
            return "evidential edge requires confidence"
    if spec["family"] == LINEAGE:
        lin = [e for e in existing_edges
               if EDGE_SPEC.get(e["type"], {}).get("family") == LINEAGE]
        if would_create_cycle(src, dst, lin):
            return "would create cycle"
    return None
