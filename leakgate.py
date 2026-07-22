"""leakgate — phase-2 validation gate, end to end. See README.md "Validation".

The README's tier-1 leak test made executable: run the real pipeline over a
generated corpus and assert that no planted identifier reaches the wire. Spans
layer 1 (ingest -> textget -> extract) into layer 2 (Redactor.to_packet), then
serialises the packet exactly as the proxy would and checks it against the
corpus manifest. Offline-runnable: with no local model, extraction is unverified
and the redactor fails every record closed, so the honest offline result is a
0% crossing rate — which the report states rather than hides, because a gate
that holds everything back also "passes" and is also broken.

    python3 leakgate.py    # self-check: generate corpus, run gate offline, assert no leaks
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from postdoc import Redactor
from ingest import ingest
from textget import extract_text
from extract import extract, preflag

SCRATCH = (
    "/private/tmp/claude-501/-Users-anthonymachula-code-career-ops/"
    "e88abacf-71b5-448a-b488-e36d3d4fbb38/scratchpad"
)


@dataclass
class Report:
    crossed: int  # records that reached the wire
    held: dict[str, int]  # held-back reason -> count (histogram, ref stripped)
    leaks: list[str]  # manifest strings found on the wire, case-insensitive. MUST be []
    crossing_rate: float  # crossed / (crossed + held); 0.0 is a failure mode, not a pass


def run_gate(corpus_dir, manifest_path, live_model: bool = False) -> Report:
    """Pipeline ingest->textget->extract->redact, then check the serialised packet
    against every planted string in the manifest. Never asserts — it reports; the
    caller decides the exit code. Offline (live_model=False) every record fails
    closed to unverified_extraction, so nothing crosses.
    """
    idents = [i["value"] for i in json.loads(Path(manifest_path).read_text())["identifiers"]]

    # manifest.json is ground truth, not a corpus document — don't ingest it.
    records = [
        r for r in ingest(str(corpus_dir)).records
        if os.path.basename(r.path) != "manifest.json"
    ]

    processed = []
    for r in records:
        text, _status = extract_text(r.path)
        r = replace(r, summary=text)  # the document text is what the gate must scrub
        if live_model:
            r = extract(r, text)  # model fills fields; itself fails closed to unverified
        else:
            # No model verified this extraction, so treat it as PHI and fail closed —
            # unverified_extraction is not a HANDLED_FLAG, so the redactor holds it back.
            r = replace(r, flags=preflag(text) + ["unverified_extraction"])
        processed.append(r)

    packet = Redactor().to_packet(processed)
    wire = json.dumps(asdict(packet))

    low = wire.lower()
    leaks = sorted({v for v in idents if v.lower() in low})

    held: dict[str, int] = {}
    for h in packet.held_back:
        reason = h.split(": ", 1)[1] if ": " in h else h  # drop the per-record ref prefix
        held[reason] = held.get(reason, 0) + 1

    crossed = len(packet.records)
    total = crossed + len(packet.held_back)
    return Report(
        crossed=crossed,
        held=held,
        leaks=leaks,
        crossing_rate=crossed / total if total else 0.0,
    )


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    import sys

    from corpus import generate

    corpus_dir = Path(SCRATCH) / "leakgate_corpus"
    generate(corpus_dir, seed=0, n=12)
    report = run_gate(corpus_dir, corpus_dir / "manifest.json", live_model=False)

    print(f"crossed:       {report.crossed}")
    print(f"held:          {sum(report.held.values())}")
    for reason, count in sorted(report.held.items()):
        print(f"                 {count:>3}  {reason}")
    print(f"leaks:         {len(report.leaks)}")
    for leak in report.leaks:
        print(f"                 LEAK: {leak!r}")
    print(f"crossing_rate: {report.crossing_rate:.0%}")

    # The assertion that IS the product: nothing planted reached the wire.
    if report.leaks:
        print(f"FAIL — {len(report.leaks)} manifest identifier(s) crossed the proxy")
        sys.exit(1)

    # README: a gate that holds everything back also "passes" and is also broken.
    if report.crossing_rate == 0.0:
        print(
            "WARNING: crossing_rate 0% — offline the gate holds every record back "
            "(unverified_extraction). Zero leaks here is trivial, not a real pass; "
            "the live-model gate is what proves the scrubbing itself works."
        )

    # Offline, honestly: no model verified anything, so all 12 fail closed.
    assert report.leaks == [], report.leaks
    assert report.crossed == 0, report.crossed
    assert sum(report.held.values()) == 12, report.held
    assert 0.0 <= report.crossing_rate <= 1.0, report.crossing_rate
    assert all("unverified_extraction" in reason for reason in report.held), report.held

    print(
        f"ok — 12 records, {report.crossed} crossed, "
        f"{sum(report.held.values())} held (all unverified offline), 0 leaks"
    )


if __name__ == "__main__":
    _demo()
