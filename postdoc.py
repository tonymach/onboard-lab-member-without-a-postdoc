"""postdoc — the contract between the three layers. See README.md.

Layer 1 (catalogue) and layer 3 (interpretation) are stubbed; they need models.
Layer 2 (redaction) is real, because it is the trust boundary and stubbing it
would make every test below meaningless.

    python3 postdoc.py    # self-check
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

# ─── layer 1: catalogue ──────────────────────────────────────────────────────
# Local only. Assume every field can contain PHI.

DOC_TYPES = ("protocol", "consent", "manuscript", "thesis", "grant", "notes", "unknown")

# Flags the redactor knows how to neutralise. Second line of defence — document-class
# gating (RISKY_DOC_TYPES, below) runs first. A flag outside this set means the
# record does not cross. Adding a flag without adding a handler is the one way to
# silently leak, so the check is on this constant, not on the handler code.
HANDLED_FLAGS = frozenset({"person_name", "mrn", "dob", "email", "phone"})

# Document classes that never cross, however clean their flags look — only their
# existence and metadata do. ML PII detection drops to ~0.41 F1 on clinical text,
# so the primary control is this gate, not entity redaction. Clinical-measure
# types join the set once they exist.
RISKY_DOC_TYPES = frozenset({"consent"})


@dataclass
class FileRecord:
    path: str
    sha256: str
    doc_type: str
    summary: str
    people: list[str] = field(default_factory=list)
    project: str | None = None
    title: str | None = None
    date: str | None = None
    flags: list[str] = field(default_factory=list)


# ─── layer 2: redaction ──────────────────────────────────────────────────────

PATTERNS = {
    "mrn": re.compile(r"\b(?:MRN|PHN|HCN)[:\s#]*[A-Z0-9-]{4,}\b", re.I),
    "alnum_id": re.compile(r"\b[A-Z]{1,4}\d{5,}[A-Z]{0,3}\b", re.I),
    "long_id": re.compile(r"\b\d{6,12}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "phone": re.compile(r"\b(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),
    "dob": re.compile(r"\b(?:DOB|born)[:\s]*\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b", re.I),
    # Period required for mr/mrs/ms so "MR imaging" in a motor-control lab survives.
    "honorific": re.compile(r"\b(?:(?:dr|prof)\.?|(?:mr|mrs|ms)\.)\s+\w+(?:-\w+)*", re.I),
}
# ponytail: regex matches typed patterns; it does not detect identifiers. Two
# red-team rounds fixed what was cheap and left a named residual class: a bare
# 1994-03-02 in the date field (indistinguishable from a document date), unlisted
# names ("Bob Smith" as a project value, "Dr. Van Helsing"'s second surname word,
# a "-Park" appended to a known name), and bare numerics under 6 digits. Survivable
# now because RISKY_DOC_TYPES holds the worst documents back whole; GLiNER-PII
# inside Presidio is the phase-2 upgrade path for the entire class. Do not keep
# widening these patterns — that arms race is unwinnable and PLAN.md says so.


@dataclass
class SafeRecord:
    ref: str  # sha256[:12]; only the local machine can turn this back into a path
    doc_type: str
    summary: str
    people: list[str]  # PERSON_01, ...
    project: str | None
    title: str | None
    date: str | None


@dataclass
class SafePacket:
    records: list[SafeRecord]
    held_back: list[str]  # refs that failed closed, with reason


class Redactor:
    """Pseudonymises records on their way out. The name map never leaves here."""

    def __init__(self) -> None:
        self._alias: dict[str, str] = {}

    def _alias_for(self, name: str) -> str:
        if name not in self._alias:
            self._alias[name] = f"PERSON_{len(self._alias) + 1:02d}"
        return self._alias[name]

    def _scrub(self, text: str, people: list[str]) -> str:
        # Longest first, so "Jane Chen" is replaced before "Chen" is.
        for name in sorted(people, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(name)}\b", self._alias_for(name), text, flags=re.I)
            # ponytail: surnames only. A doc calling her "Jane" and never "Jane Chen"
            # keeps the first name. Swap in a real NER pass when a lab hits that.
            parts = name.split()
            if len(parts) > 1:
                text = re.sub(
                    rf"\b{re.escape(parts[-1])}\b", self._alias_for(name), text, flags=re.I
                )
                # "JaneChen"/"JANECHEN" — concatenation defeats \b, red-team found it.
                text = re.sub(
                    re.escape("".join(parts)), self._alias_for(name), text, flags=re.I
                )
        for pattern in PATTERNS.values():
            text = pattern.sub("[REDACTED]", text)
        return text

    def to_packet(self, records: list[FileRecord]) -> SafePacket:
        safe, held = [], []
        for r in records:
            ref = r.sha256[:12]
            if r.doc_type in RISKY_DOC_TYPES:
                held.append(f"{ref}: risky doc type {r.doc_type!r}")
                continue
            if r.doc_type not in DOC_TYPES:
                held.append(f"{ref}: unknown doc type {r.doc_type!r}")
                continue
            unknown = set(r.flags) - HANDLED_FLAGS
            if unknown:
                held.append(f"{ref}: unhandled {sorted(unknown)}")
                continue
            # Every outgoing string is scrubbed or aliased. Layer 1 is a local
            # model, i.e. untrusted input — a name can land in any field.
            safe.append(
                SafeRecord(
                    ref=ref,
                    doc_type=r.doc_type,
                    summary=self._scrub(r.summary, r.people),
                    people=[self._alias_for(p) for p in r.people],
                    project=self._scrub(r.project, r.people) if r.project else None,
                    title=self._scrub(r.title, r.people) if r.title else None,
                    date=self._scrub(r.date, r.people) if r.date else None,
                )
            )
        return SafePacket(records=safe, held_back=held)

    def rehydrate(self, text: str) -> str:
        """Alias -> real name. Local only, for a viewer cleared to see them."""
        for name, alias in self._alias.items():
            text = text.replace(alias, name)
        return text


# ─── layer 3: interpretation ─────────────────────────────────────────────────
# Consumes a SafePacket, never a FileRecord.

BLOCK_KINDS = ("project", "person", "protocol", "dataset")
EDGE_RELS = ("used", "produced", "supervised", "supersedes")


@dataclass
class Block:
    id: str
    kind: str
    label: str
    detail: str


@dataclass
class Edge:
    src: str
    dst: str
    rel: str


def interpret(packet: SafePacket) -> tuple[list[Block], list[Edge]]:
    """Stub. A frontier model does this for real; the shape is what matters now."""
    blocks = [
        Block(id=p, kind="project", label=p, detail="")
        for p in sorted({r.project for r in packet.records if r.project})
    ]
    seen = {b.id for b in blocks}
    edges = []
    for r in packet.records:
        for person in r.people:
            if person not in seen:
                blocks.append(Block(id=person, kind="person", label=person, detail=""))
                seen.add(person)
            if r.project:
                edges.append(Edge(src=person, dst=r.project, rel="produced"))
    return blocks, edges


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    records = [
        FileRecord(
            path="/Volumes/LabHD/reach study/consent_v3_FINAL.docx",
            sha256="a" * 64,
            doc_type="consent",
            title="Consent — Jane Chen",
            summary="Signed by Jane Chen, MRN 4820193, DOB 1994-03-02. Chen ran the reach block.",
            people=["Jane Chen"],
            project="reach-adaptation",
            flags=["person_name", "mrn", "dob"],
        ),
        FileRecord(
            path="/Volumes/LabHD/reach study/thesis_draft.pdf",
            sha256="b" * 64,
            doc_type="thesis",
            summary="Jane Chen's thesis on visuomotor adaptation. Contact jane@uni.ca.",
            people=["Jane Chen"],
            project="reach-adaptation",
            flags=["person_name", "email"],
        ),
        FileRecord(
            path="/Volumes/LabHD/reach study/methods_draft.docx",
            sha256="d" * 64,
            doc_type="manuscript",
            summary="Methods section for the adaptation paper. Reviewed by Dr. Ramirez, "
            "cc dr. singh. See JANECHEN thesis; sample E1234567, subject 482019, "
            "batch ABCD12345.",
            people=["Jane Chen"],
            project="Jane Chen reach study",  # layer 1 put a name in a metadata field
            flags=["person_name"],
        ),
        FileRecord(
            path="/Volumes/LabHD/misc/scan_sheet.pdf",
            sha256="c" * 64,
            doc_type="unknown",
            summary="Something with a face photo in it.",
            people=[],
            flags=["face_photo"],  # no handler exists
        ),
    ]

    r = Redactor()
    packet = r.to_packet(records)
    wire = json.dumps(asdict(packet))

    # The only test that really matters: nothing identifying is on the wire.
    # Ramirez, singh, E1234567, 482019, ABCD12345 and the JANECHEN casing are
    # red-team regression cases. Case-insensitive, so case-mangling can't pass.
    for leak in (
        "Jane", "Chen", "4820193", "1994-03-02", "jane@uni.ca",
        "Ramirez", "singh", "E1234567", "482019", "ABCD12345",
    ):
        assert leak.lower() not in wire.lower(), f"LEAK: {leak!r} crossed the proxy"

    # Finding 1: a consent form is held back even with every flag handled.
    assert any("risky doc type" in h for h in packet.held_back), packet.held_back

    # Fails closed on a flag it has no handler for.
    assert any("face_photo" in h for h in packet.held_back), packet.held_back
    assert len(packet.records) == 2, packet

    # Metadata fields are scrubbed too — layer 1 output is untrusted.
    assert packet.records[1].project == "PERSON_01 reach study", packet.records[1]

    # Same person, same alias, across files.
    assert packet.records[0].people == packet.records[1].people == ["PERSON_01"]

    # Paths never cross; refs are one-way without the local machine.
    assert "LabHD" not in wire

    # Rehydration is exact, and local.
    assert r.rehydrate("PERSON_01 ran the reach block") == "Jane Chen ran the reach block"

    blocks, edges = interpret(packet)
    assert {b.kind for b in blocks} == {"project", "person"}
    assert Edge("PERSON_01", "reach-adaptation", "produced") in edges

    print(f"ok — {len(packet.records)} records crossed, {len(packet.held_back)} held back")
    print(f"     {len(blocks)} blocks, {len(edges)} edges")
    print(f"     wire sample: {packet.records[0].summary}")


if __name__ == "__main__":
    _demo()
