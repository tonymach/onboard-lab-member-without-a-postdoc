"""wizard — the guided run. See README.md.

One prompt per hop of the pipeline, in the order cli.py lays them out. Every
step calls the same cli.cmd_* function the flags do; this module adds prompts
and nothing else, so there is no second code path to keep honest.

    python3 cli.py guide      # guided run (or: python3 wizard.py go)
    python3 wizard.py         # self-check

ponytail: input() and numbered menus, no curses, no prompt_toolkit — a menu is
a print and a read. Arrow-key selection is polish, not a feature.
"""

from __future__ import annotations

import os
import sys
import types

import cli
import tui

# Injection seam: the self-check drives the whole wizard through a canned script.
_input = input

WORKDIR = os.path.expanduser("~/postdoc-run")


def _ask(prompt: str, default: str = "") -> str:
    hint = tui.dim(f" [{default}]") if default else ""
    try:
        got = _input(tui.g(f"{prompt}{hint} > ")).strip()
    except EOFError:
        return default
    return got or default


def _yes(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    got = _ask(f"{prompt} ({d})").lower()
    return default if not got else got.startswith("y")


def _menu(title: str, options: list[tuple[str, str]]) -> str:
    """options: (key, label). Returns the chosen key."""
    print(tui.g(title))
    for i, (_key, label) in enumerate(options, 1):
        print(tui.dim(f"  {i}) ") + tui.g(label))
    while True:
        got = _ask("choose", "1")
        if got.isdigit() and 1 <= int(got) <= len(options):
            return options[int(got) - 1][0]
        for key, _label in options:
            if got.lower() == key:
                return key
        print(tui.warn("pick a number from the list"))


def _step(n: int, title: str) -> None:
    print()
    print(tui.rule())
    print(tui.bright(f" STEP {n} ") + tui.g(title))
    print(tui.rule())


def run() -> int:
    print(tui.banner())
    print()
    print(tui.g("A guided run. Everything stays on this machine; nothing is sent."))
    print(tui.dim("Ctrl-C quits at any prompt. Artifacts land in " + WORKDIR))

    os.makedirs(WORKDIR, exist_ok=True)
    records = os.path.join(WORKDIR, "records.json")
    packet = os.path.join(WORKDIR, "packet.json")

    # ── 1. what to look at ───────────────────────────────────────────────────
    _step(1, "pick a folder")
    print(tui.dim("A folder of documents — docx, pdf, txt, md. Start small: live"))
    print(tui.dim("extraction runs a local model once per document."))
    root = os.path.expanduser(_ask("folder", "~/Documents"))
    if not os.path.isdir(root):
        print(tui.bad(f"not a folder: {root}"))
        return 1

    # Count before hashing — a research drive can hold 40k files, and finding
    # that out halfway through a scan is how people Ctrl-C and never come back.
    n = sum(len(files) for _r, _d, files in os.walk(root))
    print(tui.g(f"{n} files under that folder."))
    if n > 2000:
        print(tui.warn("That is a lot. Hashing takes a while, and live extraction"))
        print(tui.warn("runs the model once per document. A subfolder is a better start."))
        if not _yes("scan it anyway", False):
            return 0

    # ── 2. scan ──────────────────────────────────────────────────────────────
    _step(2, "scan — walk, hash, dedupe (local, no model)")
    cli.cmd_scan(types.SimpleNamespace(root=root, out=records))
    print(tui.warn("records.json holds raw text. It IS PHI. It never leaves this machine."))
    if not _yes("continue"):
        return 0

    # ── 3. extract ───────────────────────────────────────────────────────────
    _step(3, "extract — what kind of document is each one?")
    mode = _menu("How should fields be filled?", [
        ("live", "live — local Ollama model reads each document (slower, useful)"),
        ("offline", "offline — no model; every record fails closed and is held back"),
    ])
    if mode == "live":
        print(tui.dim("running the local model — a few seconds per document…"))
    cli.cmd_extract(types.SimpleNamespace(records=records, live=(mode == "live")))

    # ── 4. the proxy ─────────────────────────────────────────────────────────
    _step(4, "packet — the proxy decides what may cross")
    print(tui.dim("Consent forms are held by class. Names become PERSON_nn."))
    cli.cmd_packet(types.SimpleNamespace(records=records, out=packet))

    # ── 5. the gate ──────────────────────────────────────────────────────────
    _step(5, "gate — you review exactly what would cross")
    print(tui.dim("This is the human check. Read it like a PI would."))
    if not _yes("show the review", True):
        return 0
    # The wizard owns every prompt, so it calls approve directly rather than
    # cmd_gate, whose own input() belongs to the flag path.
    import approve as approve_mod
    loaded = approve_mod._load_packet(packet)
    print(approve_mod.review(loaded))
    approved = approve_mod.approve(
        loaded, _yes("approve this packet for send", False),
        os.path.join(WORKDIR, "audit.jsonl")) is not None
    print(tui.bright("approved") if approved else tui.bad("denied"))
    if not approved:
        print(tui.warn("denied — nothing downstream runs. That is the gate working."))
        return 0

    # ── 6. the payoff ────────────────────────────────────────────────────────
    while True:
        _step(6, "the onboarding artifact")
        choice = _menu("What now?", [
            ("graph", "graph — the lab as blocks and edges"),
            ("talk", "talk — narrated walkthrough, typed questions"),
            ("voice", "voice — same, spoken aloud (push-to-talk if whisper is installed)"),
            ("quit", "quit"),
        ])
        if choice == "quit":
            print(tui.dim(f"artifacts in {WORKDIR} — packet.json is the redacted wire form"))
            return 0
        if choice == "graph":
            cli.cmd_graph(types.SimpleNamespace(packet=packet, live=False))
        else:
            cli.cmd_talk(types.SimpleNamespace(packet=packet, voice=(choice == "voice")))


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    """Drives the whole wizard through canned answers — no TTY, no model."""
    global _input, WORKDIR
    from pathlib import Path

    from corpus import generate
    from leakgate import SCRATCH

    scratch = Path(SCRATCH) / "wizard_demo"
    corpus_dir = scratch / "corpus"
    generate(corpus_dir, seed=0, n=6)

    real_input, real_workdir = _input, WORKDIR
    WORKDIR = str(scratch / "out")
    # folder, continue, extract=offline(2), show review, approve, then quit(4)
    script = iter([str(corpus_dir), "y", "2", "y", "y", "4"])
    _input = lambda _prompt: next(script)
    try:
        assert run() == 0
        packet_path = os.path.join(WORKDIR, "packet.json")
        assert os.path.exists(packet_path), "wizard produced no packet"
        import approve
        packet = approve._load_packet(packet_path)
        assert packet.records == [] and packet.held_back, \
            "offline run must hold every record back"
        assert os.path.exists(os.path.join(WORKDIR, "audit.jsonl")), "no audit line"
    finally:
        _input, WORKDIR = real_input, real_workdir

    # a menu resolves both a number and a key, and re-prompts on nonsense
    script = iter(["banana", "2"])
    _input = lambda _prompt: next(script)
    try:
        assert _menu("t", [("a", "A"), ("b", "B")]) == "b"
    finally:
        _input = real_input

    print("ok — guided run drives scan->extract->packet->gate offline, 0 crossed")


if __name__ == "__main__":
    # Any arg -> the guided run; bare -> self-check. Mirrors cli.py and approve.py.
    sys.exit(run() if len(sys.argv) > 1 else (_demo() or 0))
