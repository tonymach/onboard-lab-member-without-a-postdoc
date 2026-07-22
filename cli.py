"""cli — the wiring. argparse over the nine layer modules. See README.md.

One subcommand per hop of the pipeline, in the order the README lays them out:

    scan ROOT      layer 1: ingest + textget -> local FileRecords (may hold PHI)
    extract RECS   layer 2 prep: local model fills fields; offline = fail-closed
    packet RECS    the PROXY: Redactor.to_packet -> pseudonymised SafePacket
    gate PACKET    the PI gate — nothing downstream runs without a logged decision
    graph PACKET   layer 3: synthesize + graphview render
    talk PACKET    layer 3/4: narrate + question repl
    leakgate       phase-2 validation: generate corpus, assert no planted leak crosses

records.json is layer-1 output: it carries raw document text and IS PHI, so it is
written 0600 (owner read/write only). packet.json is the redacted wire form — the
one artifact designed to cross the proxy.

    python3 cli.py <command> --help
    python3 cli.py                 # self-check (offline, no models, no network)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

import approve
from postdoc import FileRecord, Redactor
from ingest import ingest, stats as ingest_stats
from textget import extract_text
from extract import extract, preflag
from synthesize import synthesize, FakeBackend, AnthropicBackend
from graphview import render
from walkthrough import narrate, repl
from leakgate import run_gate, SCRATCH
from corpus import generate


# ─── local artifact I/O ──────────────────────────────────────────────────────

def _dump_600(path: str, obj) -> None:
    """Write JSON owner-only. PHI-bearing artifacts (records.json) go through here."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)
    # ponytail: chmod after, because O_CREAT's mode is ignored for a file that
    # already exists — a records.json left looser by an earlier tool gets tightened.
    os.chmod(path, 0o600)


def _write_records(path: str, records: list[FileRecord]) -> None:
    _dump_600(path, {"records": [asdict(r) for r in records]})


def _read_records(path: str) -> list[FileRecord]:
    with open(path) as f:
        return [FileRecord(**r) for r in json.load(f)["records"]]


# ─── commands ────────────────────────────────────────────────────────────────

def cmd_scan(args) -> int:
    result = ingest(args.root)
    # textget fills each record's text into summary — the field to_packet scrubs and
    # extract reads. Mirrors leakgate.run_gate's replace(r, summary=text).
    records = [replace(r, summary=extract_text(r.path)[0]) for r in result.records]
    _write_records(args.out, records)
    print(ingest_stats(result))
    print(f"wrote {len(records)} records -> {args.out} (0600, may contain PHI)")
    return 0


def cmd_extract(args) -> int:
    records = _read_records(args.records)
    out = []
    for r in records:
        if args.live:
            out.append(extract(r, r.summary))  # Ollama; itself fails closed to unverified
        else:
            # No model verified this -> unverified_extraction (not a HANDLED_FLAG),
            # so the redactor holds it back. Silence is treated as PHI.
            out.append(replace(r, flags=preflag(r.summary) + ["unverified_extraction"]))
    _write_records(args.records, out)  # in place
    unverified = sum("unverified_extraction" in r.flags for r in out)
    print(f"extracted {len(out)} records ({'live' if args.live else 'offline'}); "
          f"{unverified} unverified (will be held back)")
    return 0


def cmd_packet(args) -> int:
    packet = Redactor().to_packet(_read_records(args.records))
    # packet.json is the redacted wire form — safe by construction, default perms.
    with open(args.out, "w") as f:
        json.dump(asdict(packet), f, indent=2)
    print(f"{len(packet.records)} crossed, {len(packet.held_back)} held back -> {args.out}")
    return 0


def cmd_gate(args) -> int:
    packet = approve._load_packet(args.packet)
    print(approve.review(packet))
    decision = args.yes or input("\nApprove for send? [y/N] ").strip().lower() == "y"
    result = approve.approve(packet, decision, args.log)
    print("\napproved" if result is not None else "\ndenied")
    return 0 if result is not None else 1


def _backend(live: bool):
    if live and os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicBackend()
    if live:
        print("cli: --live set but ANTHROPIC_API_KEY missing; using FakeBackend",
              file=sys.stderr)
    return FakeBackend()


def cmd_graph(args) -> int:
    packet = approve._load_packet(args.packet)
    print(render(*synthesize(packet, _backend(args.live))))
    return 0


def cmd_talk(args) -> int:
    packet = approve._load_packet(args.packet)
    blocks, edges = synthesize(packet, FakeBackend())  # offline graph over the approved packet
    for line in narrate(blocks, edges):
        print(line)
    print()
    repl(blocks, edges)
    return 0


def cmd_leakgate(args) -> int:
    corpus_dir = Path(SCRATCH) / "cli_leakgate"
    generate(corpus_dir, seed=0, n=12)
    report = run_gate(corpus_dir, corpus_dir / "manifest.json", live_model=args.live)
    print(f"crossed:       {report.crossed}")
    print(f"held:          {sum(report.held.values())}")
    for reason, count in sorted(report.held.items()):
        print(f"                 {count:>3}  {reason}")
    print(f"leaks:         {len(report.leaks)}")
    for leak in report.leaks:
        print(f"                 LEAK: {leak!r}")
    print(f"crossing_rate: {report.crossing_rate:.0%}")
    if report.leaks:
        print(f"FAIL — {len(report.leaks)} manifest identifier(s) crossed the proxy")
        return 1
    if report.crossing_rate == 0.0:
        print("WARNING: 0% crossing — offline every record fails closed; zero leaks "
              "here is trivial. The --live gate proves the scrubbing itself.")
    return 0


# ─── argparse ────────────────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cli", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="layer 1: walk ROOT, emit local FileRecords")
    p.add_argument("root")
    p.add_argument("-o", "--out", default="records.json",
                   help="output path (written 0600 — MAY CONTAIN PHI)")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("extract", help="fill fields via local model; offline = fail-closed")
    p.add_argument("records", help="records.json from scan (rewritten in place, 0600 — PHI)")
    p.add_argument("--live", action="store_true", help="use Ollama llama3.1:8b (default: offline)")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("packet", help="the proxy: redact records into a SafePacket")
    p.add_argument("records", help="records.json (PHI in, pseudonyms out)")
    p.add_argument("-o", "--out", default="packet.json", help="output SafePacket (redacted wire form)")
    p.set_defaults(func=cmd_packet)

    p = sub.add_parser("gate", help="PI approval gate — logs a decision, exits 0=approved 1=denied")
    p.add_argument("packet", help="packet.json from packet")
    p.add_argument("--log", default="approve_log.jsonl", help="audit log path (appended)")
    p.add_argument("--yes", action="store_true", help="approve without prompting")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("graph", help="layer 3: interpret the packet into a graph and render it")
    p.add_argument("packet", help="packet.json from packet")
    p.add_argument("--live", action="store_true",
                   help="use AnthropicBackend if ANTHROPIC_API_KEY set (default: FakeBackend)")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("talk", help="narrate the graph and answer questions (interactive)")
    p.add_argument("packet", help="packet.json from packet")
    p.set_defaults(func=cmd_talk)

    p = sub.add_parser("leakgate", help="phase-2 validation: assert no planted identifier crosses")
    p.add_argument("--live", action="store_true", help="use Ollama for extraction (default: offline)")
    p.set_defaults(func=cmd_leakgate)
    return ap


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    return args.func(args)


# ─── self-check ──────────────────────────────────────────────────────────────
# Offline end to end: corpus -> scan -> extract -> packet -> gate --yes -> graph
# -> leakgate, plus the graph/talk wiring proven on a hand-built crossing packet.
# No network, no models, no TTY (gate uses --yes; talk's repl is not entered).

def _demo() -> None:
    import stat
    import tempfile
    import types

    from postdoc import SafePacket, SafeRecord

    scratch = Path(SCRATCH) / "cli_demo"
    corpus_dir = scratch / "corpus"
    generate(corpus_dir, seed=0, n=12)

    with tempfile.TemporaryDirectory() as tmp:
        recs = os.path.join(tmp, "records.json")
        pkt = os.path.join(tmp, "packet.json")
        log = os.path.join(tmp, "audit.jsonl")

        assert cmd_scan(types.SimpleNamespace(root=str(corpus_dir), out=recs)) == 0
        assert stat.S_IMODE(os.stat(recs).st_mode) == 0o600, "records.json not 0600"
        scanned = _read_records(recs)
        assert scanned and any(r.summary for r in scanned), "scan attached no text"

        assert cmd_extract(types.SimpleNamespace(records=recs, live=False)) == 0
        assert all("unverified_extraction" in r.flags for r in _read_records(recs)), \
            "offline extract must fail every record closed"

        assert cmd_packet(types.SimpleNamespace(records=recs, out=pkt)) == 0
        packet = approve._load_packet(pkt)
        assert packet.records == [] and packet.held_back, \
            "offline, every record must be held back (unverified)"

        # gate --yes: returns 0 and writes exactly one audit line.
        assert cmd_gate(types.SimpleNamespace(packet=pkt, log=log, yes=True)) == 0
        with open(log) as f:
            assert len(f.readlines()) == 1, "gate wrote no audit line"

        # graph over the (empty) offline packet: renders valid mermaid, no crash.
        assert cmd_graph(types.SimpleNamespace(packet=pkt, live=False)) == 0

        # graph/talk wiring proven on a crossing packet (offline nothing crosses,
        # so build one by hand — pseudonyms only, the wire form).
        rich = os.path.join(tmp, "rich.json")
        with open(rich, "w") as f:
            json.dump(asdict(SafePacket(
                records=[SafeRecord("aaaaaaaaaaaa", "thesis", "PERSON_01 thesis",
                                    ["PERSON_01"], "reach-adaptation", None, None)],
                held_back=[])), f)
        graph = synthesize(approve._load_packet(rich), FakeBackend())
        assert "reach-adaptation" in render(*graph), "graph command lost its content"
        assert any("active project" in l for l in narrate(*graph)), "talk narration empty"

        # leakgate offline: zero planted identifiers cross, exit 0.
        assert cmd_leakgate(types.SimpleNamespace(live=False)) == 0

    print("ok — scan->extract->packet->gate->graph->leakgate wired, offline, 0 leaks, "
          "records.json 0600")


if __name__ == "__main__":
    # Args -> real CLI; no args -> offline self-check. Mirrors approve.py.
    sys.exit(main() if len(sys.argv) > 1 else (_demo() or 0))
