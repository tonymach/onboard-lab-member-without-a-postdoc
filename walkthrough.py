"""walkthrough — layer 3/4 lite. See README.md.

Turns an already-approved (blocks, edges) graph into a spoken-style narration and
answers keyword questions against it. No model, no speech: this is the narrated
path a human reads or a TTS engine reads aloud, not the two-way conversation.

    python3 walkthrough.py    # self-check

ponytail: no LLM here — real conversation (follow-ups, paraphrase, clarification)
is phase 4 proper, layered on top once interpretation earns trust.
ponytail: no STT/TTS here — narrate() returns strings a TTS could read; wiring an
actual voice engine (Whisper/Parakeet in, Kokoro/ElevenLabs out per the README
stack) is phase 4 proper, not this module's job.
"""

from __future__ import annotations

from postdoc import Block, Edge

NOT_FOUND = "That's not in the approved packet."


# ─── narration ────────────────────────────────────────────────────────────────

def narrate(blocks: list[Block], edges: list[Edge]) -> list[str]:
    """Projects first, then per project who produced/supervised it, then its
    datasets and protocols. Short declarative sentences — TTS-friendly, no
    subordinate clauses to trip a speech engine."""
    by_id = {b.id: b for b in blocks}
    projects = sorted((b for b in blocks if b.kind == "project"), key=lambda b: b.label)

    if not projects:
        return ["This lab has no active projects in the approved packet."]

    lines = [
        f"This lab has {len(projects)} active project"
        f"{'s' if len(projects) != 1 else ''}: {', '.join(p.label for p in projects)}."
    ]

    for p in projects:
        if p.detail:
            lines.append(f"{p.label}: {p.detail}")
        for e in edges:
            if e.dst != p.id:
                continue
            who = by_id.get(e.src)
            if who is None:
                continue
            if e.rel == "produced":
                lines.append(f"{who.label} produced work on {p.label}.")
            elif e.rel == "supervised":
                lines.append(f"{who.label} supervises {p.label}.")
        for e in edges:
            if e.src != p.id or e.rel != "used":
                continue
            what = by_id.get(e.dst)
            if what is None:
                continue
            if what.kind == "dataset":
                where = f", stored at {what.detail}" if what.detail else ""
                lines.append(f"{p.label} uses the {what.label} dataset{where}.")
            elif what.kind == "protocol":
                lines.append(f"Work on {p.label} follows the {what.label} protocol.")

    for e in edges:
        if e.rel == "supersedes" and e.src in by_id and e.dst in by_id:
            lines.append(f"{by_id[e.src].label} supersedes {by_id[e.dst].label}.")

    return lines


# ─── question answering ────────────────────────────────────────────────────────
# ponytail: substring/keyword match, not embeddings or an LLM — the four
# pass-condition questions are known and narrow. Swap for retrieval once phase 4
# free-form conversation lands and questions stop being predictable.

def _matching_project(q: str, projects: list[Block]) -> Block | None:
    for p in projects:
        if p.label.lower() in q:
            return p
    return None


def answer(question: str, blocks: list[Block], edges: list[Edge]) -> str:
    q = question.lower()
    by_id = {b.id: b for b in blocks}
    projects = sorted((b for b in blocks if b.kind == "project"), key=lambda b: b.label)
    target = _matching_project(q, projects)

    if "protocol" in q:
        matches = [
            e for e in edges
            if e.rel == "used" and by_id.get(e.dst, Block("", "", "", "")).kind == "protocol"
            and (target is None or e.src == target.id)
        ]
        if not matches:
            return NOT_FOUND
        return "; ".join(f"{by_id[e.src].label} follows {by_id[e.dst].label}" for e in matches) + "."

    if any(k in q for k in ("data", "dataset", "where")):
        matches = [
            e for e in edges
            if e.rel == "used" and by_id.get(e.dst, Block("", "", "", "")).kind == "dataset"
            and (target is None or e.src == target.id)
        ]
        if not matches:
            return NOT_FOUND
        parts = []
        for e in matches:
            ds, proj = by_id[e.dst], by_id[e.src]
            where = ds.detail or "an unrecorded location"
            parts.append(f"{proj.label} data lives in {ds.label}, at {where}")
        return "; ".join(parts) + "."

    if any(k in q for k in ("who", "ask", "supervis", "contact")):
        matches = [
            e for e in edges
            if e.rel in ("produced", "supervised")
            and (target is None or e.dst == target.id)
        ]
        if not matches:
            return NOT_FOUND
        parts = []
        for e in matches:
            person, proj = by_id[e.src], by_id[e.dst]
            verb = "supervises" if e.rel == "supervised" else "worked on"
            parts.append(f"{person.label} {verb} {proj.label}")
        return "; ".join(parts) + "."

    if "project" in q:
        if not projects:
            return NOT_FOUND
        return "Active projects: " + ", ".join(p.label for p in projects) + "."

    # last resort: does the question just name a block directly?
    for b in blocks:
        if b.label.lower() in q:
            return f"{b.label}: {b.detail}" if b.detail else f"{b.label} is a {b.kind} in this lab."

    return NOT_FOUND


# ─── repl ──────────────────────────────────────────────────────────────────────

def repl(blocks: list[Block], edges: list[Edge]) -> None:
    """Interactive stdin loop. Not exercised by the self-check — no TTY there."""
    print("Type a question about the lab, or 'q' to quit.")
    while True:
        try:
            q = input("> ").strip()
        except EOFError:
            break
        if q.lower() == "q":
            break
        if q:
            print(answer(q, blocks, edges))


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    blocks = [
        Block("reach-adaptation", "project", "Reach Adaptation",
              "Visuomotor adaptation study, active since 2023."),
        Block("PERSON_01", "person", "PERSON_01", ""),
        Block("PERSON_02", "person", "PERSON_02", ""),
        Block("reach-protocol-v3", "protocol", "Reach Protocol v3",
              "Standard 8-target reaching task."),
        Block("reach-protocol-v2", "protocol", "Reach Protocol v2",
              "Superseded pilot protocol."),
        Block("reach-kinematics", "dataset", "Reach Kinematics",
              "/Volumes/LabHD/reach study/data/"),
    ]
    edges = [
        Edge("PERSON_01", "reach-adaptation", "produced"),
        Edge("PERSON_02", "reach-adaptation", "supervised"),
        Edge("reach-adaptation", "reach-kinematics", "used"),
        Edge("reach-adaptation", "reach-protocol-v3", "used"),
        Edge("reach-protocol-v3", "reach-protocol-v2", "supersedes"),
    ]

    lines = narrate(blocks, edges)
    assert lines, "narration must not be empty"
    assert "Reach Adaptation" in lines[0]
    assert any("PERSON_02 supervises" in l for l in lines)
    assert any("Reach Kinematics" in l for l in lines)
    assert any("supersedes" in l for l in lines)

    # the four README pass-condition questions, each must ground in the graph
    a1 = answer("what are the active projects?", blocks, edges)
    assert "Reach Adaptation" in a1, a1

    a2 = answer("what data exists for reach adaptation and where is it?", blocks, edges)
    assert "Reach Kinematics" in a2 and "LabHD" in a2, a2

    a3 = answer("who do I ask about reach adaptation?", blocks, edges)
    assert "PERSON_01" in a3 and "PERSON_02" in a3, a3

    a4 = answer("which protocol am I working under for reach adaptation?", blocks, edges)
    assert "Reach Protocol v3" in a4, a4

    # off-graph question: honest miss, not a hallucinated answer
    miss = answer("what is the funding budget?", blocks, edges)
    assert miss == NOT_FOUND, miss

    print(f"ok — {len(lines)} narration lines, 4/4 pass-condition questions grounded, "
          "1 honest miss")


if __name__ == "__main__":
    _demo()
