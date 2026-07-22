"""corpus — validation asset, phase 2 tier-1 corpus generator. LOCAL layer, offline.

Fabricates a small set of realistic-looking lab documents (.txt and minimal .docx)
with known-planted fake identifiers, plus a manifest.json recording every planted
string and which file carries it. The manifest is the ground truth for the phase-2
leak gate described in README.md's Validation section: run the pipeline over the
generated files, assert zero manifest strings reach the wire. Exactness over
realism — the manifest only has to be *right*, not convincing.

Only this generator is committed; generated output goes to outdir, which callers
point at scratch space, never the repo.

    python3 corpus.py    # self-check: generate seed=0 twice, verify determinism
                          # and that every manifest string is actually in its file
"""

from __future__ import annotations

import io
import json
import random
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from postdoc import DOC_TYPES

# Template kinds this generator knows how to build. A subset of postdoc's DOC_TYPES
# so a typo here can't silently invent a doc_type layer 2 has never heard of.
DOC_KINDS = ("consent", "protocol", "thesis", "grant", "notes")
assert set(DOC_KINDS) <= set(DOC_TYPES), "corpus kinds drifted from postdoc.DOC_TYPES"

FIRST_NAMES = ("Jane", "Alex", "Sam", "Priya", "Marcus", "Elena", "Wei", "Fatima", "Liam", "Noor")
LAST_NAMES = ("Chen", "Ramirez", "Singh", "Novak", "Kim", "Okafor", "Silva", "Haddad", "Brennan", "Park")
PROJECTS = ("reach-adaptation", "gait-lab", "memory-encoding", "sleep-eeg", "motor-learning")
DOMAINS = ("uni.ca", "labmail.edu", "research.org")


@dataclass
class PlantedIdentifier:
    value: str  # the exact string injected — leak gate checks for this, verbatim
    kind: str  # name | mrn | mrn_bare | dob | email | phone
    file: str  # filename relative to outdir


@dataclass
class Manifest:
    seed: int
    n: int
    files: list[str]
    identifiers: list[PlantedIdentifier] = field(default_factory=list)


# ─── fake identifier fabrication ─────────────────────────────────────────────


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _digits(rng: random.Random) -> str:
    return str(rng.randint(1_000_000, 9_999_999))


def _dob(rng: random.Random) -> str:
    return f"{rng.randint(1955, 2004)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def _email(rng: random.Random, name: str) -> str:
    return f"{name.split()[0].lower()}@{rng.choice(DOMAINS)}"


def _phone(rng: random.Random) -> str:
    return f"{rng.randint(200, 999)}-{rng.randint(200, 999)}-{rng.randint(1000, 9999)}"


def _title(project: str) -> str:
    return project.replace("-", " ").title()


# ─── document templates ──────────────────────────────────────────────────────
# Each returns (paragraphs, planted) where planted is [(value, kind), ...]. Clean
# templates get placeholder text instead of a fabricated identifier — half the
# point of the corpus is documents that should cross with nothing redacted at all.


def _build_consent(clean: bool, rng: random.Random) -> tuple[list[str], list[tuple[str, str]]]:
    project = rng.choice(PROJECTS)
    lines = ["CONSENT TO PARTICIPATE IN RESEARCH", f"Study: {_title(project)}", ""]
    planted: list[tuple[str, str]] = []
    if clean:
        lines += [
            "Participant Name: ______________________",
            "MRN: ______________________",
            "Date of Birth (DOB): ______________________",
        ]
    else:
        name = _name(rng)
        mrn = f"MRN: {_digits(rng)}"
        dob = _dob(rng)
        lines += [f"Participant Name: {name}", mrn, f"Date of Birth (DOB): {dob}"]
        planted = [(name, "name"), (mrn, "mrn"), (dob, "dob")]
    lines += [
        "",
        "I have read and understood the above and voluntarily agree to participate in this study.",
        "",
        "Signature: _______________________     Date: __________",
    ]
    return lines, planted


def _build_protocol(clean: bool, rng: random.Random) -> tuple[list[str], list[tuple[str, str]]]:
    project = rng.choice(PROJECTS)
    lines = [f"PROTOCOL: {_title(project)}", "Version 1.3", "Approved by the institutional REB.", ""]
    planted: list[tuple[str, str]] = []
    if clean:
        lines += ["Principal Investigator: [PI name withheld in template]", "Contact: lab@example.org"]
    else:
        name = _name(rng)
        email = _email(rng, name)
        lines += [f"Principal Investigator: {name}", f"Contact: {email}"]
        planted = [(name, "name"), (email, "email")]
    lines += [
        "",
        "1. Recruitment",
        "Participants are recruited from the general community via flyer and referral.",
        "2. Procedure",
        "Subjects complete a series of reaching trials while kinematic data is recorded.",
    ]
    return lines, planted


def _build_thesis(clean: bool, rng: random.Random) -> tuple[list[str], list[tuple[str, str]]]:
    project = rng.choice(PROJECTS)
    lines = [f"{_title(project)}: A Thesis", ""]
    planted: list[tuple[str, str]] = []
    if clean:
        lines += ["A thesis submitted in partial fulfillment of the requirements for the degree.", ""]
    else:
        name = _name(rng)
        email = _email(rng, name)
        subj = f"E{_digits(rng)}"
        lines += [
            f"A thesis submitted by {name} in partial fulfillment of the requirements for the degree.",
            "",
            f"Correspondence: {email}",
            f"Representative subject code: {subj}",
        ]
        planted = [(name, "name"), (email, "email"), (subj, "mrn_bare")]
    lines += [
        "Abstract",
        "This thesis investigates adaptation dynamics in the sensorimotor system during "
        "repeated exposure to a visuomotor perturbation.",
    ]
    return lines, planted


def _build_grant(clean: bool, rng: random.Random) -> tuple[list[str], list[tuple[str, str]]]:
    project = rng.choice(PROJECTS)
    lines = [f"Grant Summary -- {_title(project)}", ""]
    planted: list[tuple[str, str]] = []
    if clean:
        lines += ["PI: [PI name withheld in template]", "Phone: [redacted in template]"]
    else:
        name = _name(rng)
        phone = _phone(rng)
        lines += [f"PI: {name}", f"Phone: {phone}"]
        planted = [(name, "name"), (phone, "phone")]
    lines += [
        "Budget: $250,000 over 3 years",
        "Aims: characterize adaptation dynamics across age groups and clinical populations.",
    ]
    return lines, planted


def _build_notes(clean: bool, rng: random.Random) -> tuple[list[str], list[tuple[str, str]]]:
    project = rng.choice(PROJECTS)
    lines = [f"Lab notes -- {project}", ""]
    planted: list[tuple[str, str]] = []
    if clean:
        lines += ["Ran the standard calibration sequence today. No issues."]
    else:
        name = _name(rng)
        subj = f"E{_digits(rng)}"
        lines += [f"Talked to {name} about {project} today.", f"Subject {subj} completed session 3."]
        planted = [(name, "name"), (subj, "mrn_bare")]
    lines += ["Need to recalibrate the robot before the next block."]
    return lines, planted


_BUILDERS = {
    "consent": _build_consent,
    "protocol": _build_protocol,
    "thesis": _build_thesis,
    "grant": _build_grant,
    "notes": _build_notes,
}


# ─── minimal .docx writer ────────────────────────────────────────────────────


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _minimal_docx(paragraphs: list[str]) -> bytes:
    """The three zip parts Word (and our own reader below) need. No styles, no
    core/app props. ponytail: not a spec-complete OOXML package — strict
    validators may balk. Swap in python-docx if a lab document ever needs one.
    """
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{_xml_escape(p)}</w:t></w:r></w:p>' for p in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def _docx_text(path: Path) -> str:
    """Reverse of _minimal_docx, for the self-check only — real extraction is
    layer 1's job (docling/pdfmux per README), not this generator's.
    """
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.S))
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _read_text(path: Path) -> str:
    return _docx_text(path) if path.suffix == ".docx" else path.read_text(encoding="utf-8")


# ─── generator ────────────────────────────────────────────────────────────────


def generate(outdir: str | Path, seed: int = 0, n: int = 12) -> dict:
    """Write n documents plus manifest.json into outdir. Deterministic in seed:
    same seed, same n -> byte-identical manifest (files list, identifiers, all of
    it). outdir is created if missing; existing files in it are overwritten.
    """
    rng = random.Random(seed)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    identifiers: list[PlantedIdentifier] = []
    for i in range(n):
        kind = DOC_KINDS[i % len(DOC_KINDS)]
        # ponytail: fixed cadence (not rng-drawn) so "which files are clean" is
        # legible from the filename index, not a fact you need the manifest for.
        clean = i % 3 == 2
        ext = ".docx" if i % 2 == 0 else ".txt"
        fname = f"{kind}_{i:02d}{ext}"

        paragraphs, planted = _BUILDERS[kind](clean, rng)
        for value, k in planted:
            identifiers.append(PlantedIdentifier(value=value, kind=k, file=fname))

        path = outdir / fname
        if ext == ".docx":
            path.write_bytes(_minimal_docx(paragraphs))
        else:
            path.write_text("\n".join(paragraphs) + "\n", encoding="utf-8")
        files.append(fname)

    manifest = asdict(Manifest(seed=seed, n=n, files=files, identifiers=identifiers))
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


# ─── self-check ──────────────────────────────────────────────────────────────


def _demo() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        m1 = generate(d1, seed=0, n=12)
        m2 = generate(d2, seed=0, n=12)
        assert m1 == m2, "same seed produced different manifests"

        d1p = Path(d1)
        assert (d1p / "manifest.json").exists()
        checked = 0
        for ident in m1["identifiers"]:
            text = _read_text(d1p / ident["file"])
            assert ident["value"] in text, f"planted string missing from its own file: {ident}"
            checked += 1
        assert checked > 0

        clean_files = set(m1["files"]) - {i["file"] for i in m1["identifiers"]}
        assert clean_files, "expected at least one clean (identifier-free) document"

        kinds_seen = {i["kind"] for i in m1["identifiers"]}
        assert kinds_seen == {"name", "mrn", "mrn_bare", "dob", "email", "phone"}, kinds_seen

        print(
            f"ok — {len(m1['files'])} files, {checked} planted identifiers verified present, "
            f"{len(clean_files)} clean"
        )


if __name__ == "__main__":
    _demo()
