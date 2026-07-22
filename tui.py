"""tui — the phosphor terminal skin. See README.md.

Green CRT styling for cli.py: an ASCII cover, colour helpers, a typewriter
print for narration. Pure presentation — no module below the CLI imports this,
and when stdout is a pipe (or NO_COLOR is set) every helper degrades to plain
text so nothing that parses our output ever meets an escape code.

    python3 tui.py    # self-check, no TTY needed

ponytail: raw ANSI, no curses and no rich — five escape codes cover a 1980s
phosphor look; a real TUI (panels, live meters) is a different product.
"""

from __future__ import annotations

import os
import sys
import time

_ON = (
    sys.stdout.isatty()
    and not os.environ.get("NO_COLOR")
    and os.environ.get("TERM") != "dumb"
) or os.environ.get("POSTDOC_RETRO") == "1"

_G = "\033[32m"        # phosphor green
_BR = "\033[1;92m"     # bright green — the good numbers
_DIM = "\033[2;32m"    # dim green — chrome, rules, asides
_AMB = "\033[33m"      # amber — held back, warnings
_RED = "\033[1;91m"    # red — leaks, denials
_R = "\033[0m"

_CPS_DELAY = 0.004     # typewriter: ~250 chars/sec, fast enough to not annoy


def _wrap(code: str, s: str) -> str:
    return f"{code}{s}{_R}" if _ON else s


def g(s: str) -> str:
    return _wrap(_G, s)


def bright(s: str) -> str:
    return _wrap(_BR, s)


def dim(s: str) -> str:
    return _wrap(_DIM, s)


def warn(s: str) -> str:
    return _wrap(_AMB, s)


def bad(s: str) -> str:
    return _wrap(_RED, s)


def rule(width: int = 62) -> str:
    return dim("─" * width)


_COVER = r"""
██████╗  ██████╗ ███████╗████████╗██████╗  ██████╗  ██████╗
██╔══██╗██╔═══██╗██╔════╝╚══██╔══╝██╔══██╗██╔═══██╗██╔════╝
██████╔╝██║   ██║███████╗   ██║   ██║  ██║██║   ██║██║
██╔═══╝ ██║   ██║╚════██║   ██║   ██║  ██║██║   ██║██║
██║     ╚██████╔╝███████║   ██║   ██████╔╝╚██████╔╝╚██████╗
╚═╝      ╚═════╝ ╚══════╝   ╚═╝   ╚═════╝  ╚═════╝  ╚═════╝"""


def banner() -> str:
    lines = [g(l) for l in _COVER.strip("\n").split("\n")]
    lines.append(dim("· a digital post-doc · nothing identifying leaves this machine ·"))
    lines.append(dim("[ trust boundary online — fails closed ]"))
    return "\n".join(lines)


def type_out(s: str, end: str = "\n") -> None:
    """Typewriter print. Instant when styling is off — pipes get plain lines."""
    if not _ON:
        print(s, end=end)
        return
    for ch in f"{_G}{s}{_R}":
        sys.stdout.write(ch)
        if ch not in "\033[m0129;":  # don't sleep inside escape sequences
            sys.stdout.flush()
            time.sleep(_CPS_DELAY)
    sys.stdout.write(end)
    sys.stdout.flush()


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    if not sys.stdout.isatty() and os.environ.get("POSTDOC_RETRO") != "1":
        # piped: every helper must be a passthrough with zero escape codes
        assert g("x") == "x" and bright("x") == "x" and bad("x") == "x"
        assert "\033" not in banner()
    assert "██" in banner() and "post-doc" in banner()
    assert "─" in rule()
    type_out("narration line renders")  # instant when off, typed when on
    print("ok — phosphor skin wired, "
          + ("styling live" if _ON else "plain passthrough (no TTY)"))


if __name__ == "__main__":
    _demo()
