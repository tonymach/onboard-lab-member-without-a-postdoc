"""approve — phase 5, the PI gate. Consumes a SafePacket, nothing else.

Nothing reaches a newcomer without explicit approval. review() renders the
packet exactly as it will cross the wire -- held-back reasons, then every
crossing SafeRecord in full, field for field, no summarizing and no
prettifying -- because the PI has to see what a newcomer would see, not a
claim about it. approve() is the only door: it always writes an audit line,
and returns the packet on yes, None on no. Any code that sends a packet must
gate on approve()'s *return value*, never on the decision bool directly --
that's what stops a future caller from routing around the audit trail.

    python3 approve.py --packet packet.json           # interactive
    python3 approve.py --packet packet.json --yes      # non-interactive approve
    python3 approve.py                                  # self-check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict

from postdoc import SafePacket, SafeRecord


def _wire(packet: SafePacket) -> str:
    """The exact serialised form the PI is shown and the audit hash is taken over."""
    return json.dumps(asdict(packet), sort_keys=True)


def review(packet: SafePacket) -> str:
    """Human-readable report for the PI: counts, held-back reasons, then the wire.

    Every crossing record is rendered in full, exactly as it serialises --
    the PI approves what a newcomer would actually see, not a gloss of it.
    """
    lines = [
        f"{len(packet.records)} record(s) would cross, {len(packet.held_back)} held back.",
        "",
        "-- held back --",
    ]
    lines += [f"  {reason}" for reason in packet.held_back] or ["  (none)"]
    lines += ["", "-- crossing records (exactly as sent) --"]
    lines += [json.dumps(asdict(r), indent=2, sort_keys=True) for r in packet.records] or ["  (none)"]
    return "\n".join(lines)


def approve(packet: SafePacket, decision: bool, log_path: str) -> SafePacket | None:
    """The gate. Always logs; returns the packet only when decision is True."""
    line = {
        "ts": time.time(),
        "decision": decision,
        "n_crossed": len(packet.records),
        "n_held": len(packet.held_back),
        "packet_sha256": hashlib.sha256(_wire(packet).encode()).hexdigest(),
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(line) + "\n")
    return packet if decision else None


def _load_packet(path: str) -> SafePacket:
    with open(path) as f:
        data = json.load(f)
    return SafePacket(
        records=[SafeRecord(**r) for r in data["records"]],
        held_back=list(data["held_back"]),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="PI approval gate for a SafePacket.")
    ap.add_argument("--packet", required=True, help="path to a packet json file")
    ap.add_argument("--log", default="approve_log.jsonl", help="audit log path")
    ap.add_argument("--yes", action="store_true", help="approve without prompting")
    args = ap.parse_args()

    packet = _load_packet(args.packet)
    print(review(packet))

    decision = args.yes or input("\nApprove for send? [y/N] ").strip().lower() == "y"
    result = approve(packet, decision, args.log)
    print("\napproved" if result is not None else "\ndenied")
    sys.exit(0 if result is not None else 1)


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    packet = SafePacket(
        records=[
            SafeRecord(
                ref="a" * 12,
                doc_type="thesis",
                summary="PERSON_01's thesis on visuomotor adaptation.",
                people=["PERSON_01"],
                project="reach-adaptation",
                title="Thesis draft",
                date="2023-01-01",
            )
        ],
        held_back=[
            "b" * 12 + ": risky doc type 'consent'",
            "c" * 12 + ": unhandled ['face_photo']",
        ],
    )

    report = review(packet)
    for reason in packet.held_back:
        assert reason in report, f"held-back reason missing verbatim: {reason!r}"
    assert "PERSON_01" in report and "reach-adaptation" in report, report

    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "audit.jsonl")

        denied = approve(packet, False, log_path)
        assert denied is None, denied
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 1, lines
        entry = json.loads(lines[0])
        assert entry["decision"] is False
        assert entry["n_crossed"] == 1 and entry["n_held"] == 2, entry
        assert len(entry["packet_sha256"]) == 64, entry

        approved = approve(packet, True, log_path)
        assert approved is packet, approved
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2, lines
        entry2 = json.loads(lines[1])
        assert entry2["decision"] is True
        assert entry2["packet_sha256"] == entry["packet_sha256"], "same packet, same hash"

    print(
        f"ok — review() rendered {len(packet.records)} record(s) + "
        f"{len(packet.held_back)} held-back reasons; deny->None, approve->packet, "
        "2 audit lines written"
    )


if __name__ == "__main__":
    # No args -> self-check (`python3 approve.py`). Args -> real CLI.
    main() if len(sys.argv) > 1 else _demo()
