"""graphview — layer 3 presentation. See README.md.

Pure rendering: (list[Block], list[Edge]) -> str. No I/O, no model calls, no
randomness. This is the artifact a PI eyeballs for the phase-3 "yes, that's my
lab" check, so every ordering below is sorted — two calls on the same graph
must produce byte-identical output.

    python3 graphview.py    # self-check
"""

from __future__ import annotations

import re

from postdoc import Block, Edge

# Mermaid node ids may only contain [A-Za-z0-9_] and can't start with a digit.
# Block.id is free text from an LLM (project names, "PERSON_01", ...), so it
# needs a sanitised, collision-free id distinct from the human-readable label.
_UNSAFE = re.compile(r"[^A-Za-z0-9_]+")

# One shape per block kind — visual kind marker in the mermaid graph.
# ponytail: fixed 4-entry table, not a general shape registry. postdoc.BLOCK_KINDS
# is the source of truth for what kinds exist; extend this dict when it grows.
_SHAPE = {
    "project": ("[", "]"),  # rectangle
    "person": ("(", ")"),  # rounded
    "protocol": ("([", "])"),  # stadium
    "dataset": ("[(", ")]"),  # cylinder
}
_DEFAULT_SHAPE = ("[", "]")


def _sanitize_id(raw: str, used: set[str]) -> str:
    safe = _UNSAFE.sub("_", raw).strip("_") or "n"
    if safe[0].isdigit():
        safe = f"n_{safe}"
    # Collisions happen when two distinct ids sanitise to the same string
    # (e.g. "reach-1" and "reach_1"); suffix deterministically off `used`.
    candidate, i = safe, 2
    while candidate in used:
        candidate, i = f"{safe}_{i}", i + 1
    used.add(candidate)
    return candidate


def _quote(label: str) -> str:
    return '"' + label.replace('"', "'") + '"'


def _mermaid(blocks: list[Block], edges: list[Edge]) -> str:
    ordered = sorted(blocks, key=lambda b: (b.kind, b.id))
    used: set[str] = set()
    node_id = {b.id: _sanitize_id(b.id, used) for b in ordered}

    lines = ["```mermaid", "graph LR"]
    for b in ordered:
        lo, lc = _SHAPE.get(b.kind, _DEFAULT_SHAPE)
        lines.append(f"    {node_id[b.id]}{lo}{_quote(b.label)}{lc}:::{b.kind}")
    for e in sorted(edges, key=lambda e: (e.src, e.dst, e.rel)):
        if e.src not in node_id or e.dst not in node_id:
            continue  # dangling edge — a block interpret() failed to emit
        lines.append(f"    {node_id[e.src]} -->|{e.rel}| {node_id[e.dst]}")
    for kind in sorted({b.kind for b in ordered}):
        lines.append(f"    classDef {kind} fill:#eee,stroke:#333;")
    lines.append("```")
    return "\n".join(lines)


def _adjacency(blocks: list[Block], edges: list[Edge]) -> str:
    label = {b.id: b.label for b in blocks}
    incoming: dict[str, list[str]] = {b.id: [] for b in blocks}
    outgoing: dict[str, list[str]] = {b.id: [] for b in blocks}
    for e in edges:
        if e.dst in incoming:
            incoming[e.dst].append(f"  {e.rel} by {label.get(e.src, e.src)}")
        if e.src in outgoing:
            outgoing[e.src].append(f"  {e.rel} {label.get(e.dst, e.dst)}")

    lines = []
    for b in sorted(blocks, key=lambda b: (b.kind, b.id)):
        lines.append(f"{b.kind.upper()} {b.label}:")
        for line in sorted(incoming[b.id] + outgoing[b.id]):
            lines.append(line)
    return "\n".join(lines)


def render(blocks: list[Block], edges: list[Edge]) -> str:
    """Mermaid graph + plain-text adjacency listing, concatenated. Deterministic."""
    return _mermaid(blocks, edges) + "\n\n" + _adjacency(blocks, edges)


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    from postdoc import FileRecord, Redactor, interpret

    # _demo-shaped: same fields as postdoc._demo's records, small subset.
    records = [
        FileRecord(
            path="/Volumes/LabHD/reach study/thesis_draft.pdf",
            sha256="b" * 64,
            doc_type="thesis",
            summary="Jane Chen's thesis on visuomotor adaptation.",
            people=["Jane Chen"],
            project="reach-adaptation",
            flags=["person_name"],
        ),
        FileRecord(
            path="/Volumes/LabHD/reach study/methods_draft.docx",
            sha256="d" * 64,
            doc_type="manuscript",
            summary="Methods section for the adaptation paper.",
            people=["Jane Chen"],
            project="reach-adaptation",
            flags=["person_name"],
        ),
    ]
    packet = Redactor().to_packet(records)
    blocks, edges = interpret(packet)

    out1 = render(blocks, edges)
    out2 = render(blocks, edges)
    assert out1 == out2, "render is not deterministic"

    assert "```mermaid" in out1 and "graph LR" in out1, "mermaid section missing"
    assert "PROJECT reach-adaptation:" in out1, "adjacency section missing"
    assert "PERSON_01" in out1, "person block did not render"
    assert "produced by PERSON_01" in out1, "adjacency edge phrasing wrong"

    print(f"ok — rendered {len(blocks)} blocks, {len(edges)} edges, {len(out1)} chars")


if __name__ == "__main__":
    _demo()
