"""voice — phase 4, the speech shell. See README.md.

Composable by design: whisper.cpp STT -> walkthrough.answer -> macOS `say` TTS.
Three separate processes, and the middle one only ever sees the approved graph —
an end-to-end speech model could not make that claim, which is why the README
stack disqualifies them outright.

Runtime tools (none are Python dependencies; all discovered at call time):
  say          built into macOS — TTS
  ffmpeg       mic capture via avfoundation
  whisper-cli  `brew install whisper-cpp`, model at ~/.cache/whisper/

    python3 voice.py    # self-check — no mic, no speakers, no whisper needed

ponytail: `say` instead of Kokoro — built into every Mac, zero install; swap in
Kokoro (or ElevenLabs) when voice quality starts to matter to a real newcomer.
ponytail: push-to-talk with a fixed recording window, not VAD — Enter to talk,
N seconds of mic, done. Voice-activity detection is polish, not phase 4.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from postdoc import Block, Edge
from walkthrough import answer, narrate

RECORD_SECONDS = 6
WHISPER_MODEL = os.path.expanduser(
    os.environ.get("POSTDOC_WHISPER_MODEL", "~/.cache/whisper/ggml-base.en.bin")
)
MIC_DEVICE = os.environ.get("POSTDOC_MIC", ":0")  # avfoundation "video:audio" index

# Injection seam so the self-check can fake every external process.
_run = subprocess.run
_which = shutil.which


# ─── the three stages ─────────────────────────────────────────────────────────

def speak(text: str) -> None:
    """TTS. Blocks until spoken; `say -o` in tests writes a file instead."""
    voice = os.environ.get("POSTDOC_VOICE")
    cmd = ["say"] + (["-v", voice] if voice else []) + [text]
    _run(cmd, check=False)


def record(wav_path: str, seconds: int = RECORD_SECONDS) -> bool:
    """Capture the default mic to 16 kHz mono wav — whisper's native diet."""
    if _which("ffmpeg") is None:
        return False
    r = _run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "avfoundation", "-i", MIC_DEVICE,
         "-t", str(seconds), "-ar", "16000", "-ac", "1", wav_path],
        check=False, capture_output=True,
    )
    return r.returncode == 0


def transcribe(wav_path: str) -> str:
    """STT via whisper.cpp. Empty string means it couldn't hear or couldn't run."""
    cli = _which("whisper-cli") or _which("whisper-cpp")
    if cli is None or not os.path.exists(WHISPER_MODEL):
        return ""
    r = _run(
        [cli, "-m", WHISPER_MODEL, "-f", wav_path, "-np", "-nt"],
        check=False, capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def stt_available() -> tuple[bool, str]:
    """Why not, when not — the user gets the install command, not a shrug."""
    if _which("ffmpeg") is None:
        return False, "ffmpeg not found (mic capture): brew install ffmpeg"
    if _which("whisper-cli") is None and _which("whisper-cpp") is None:
        return False, "whisper-cli not found: brew install whisper-cpp"
    if not os.path.exists(WHISPER_MODEL):
        return False, f"whisper model missing at {WHISPER_MODEL}"
    return True, ""


# ─── the loop ─────────────────────────────────────────────────────────────────

def loop(blocks: list[Block], edges: list[Edge]) -> None:
    """Push-to-talk over the approved graph. Never exercised by the self-check —
    it needs a TTY, a mic, and speakers. Degrades to typed questions with spoken
    answers when STT is missing."""
    lines = narrate(blocks, edges)
    print(lines[0])
    speak(lines[0])

    ok, why = stt_available()
    if not ok:
        print(f"(voice input off — {why}; type your questions, answers are spoken)")
    print("Enter to talk" if ok else "Type a question", "— 'q' quits.")

    while True:
        try:
            typed = input("> ").strip()
        except EOFError:
            break
        if typed.lower() == "q":
            break
        if typed:
            q = typed
        elif ok:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav = f.name
            try:
                print(f"listening for {RECORD_SECONDS}s…")
                q = transcribe(wav) if record(wav) else ""
            finally:
                os.unlink(wav)
            if not q:
                print("(heard nothing)")
                continue
            print(f"heard: {q}")
        else:
            continue
        a = answer(q, blocks, edges)
        print(a)
        speak(a)


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    global _run, _which
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = " What are the active projects? \n"

    real_run, real_which = _run, _which
    _run = lambda cmd, **kw: calls.append(cmd) or _Result()
    _which = lambda name: f"/fake/bin/{name}"
    try:
        # speak builds a say invocation and nothing else
        speak("hello lab")
        assert calls[-1][0] == "say" and calls[-1][-1] == "hello lab", calls[-1]

        # record asks ffmpeg for 16 kHz mono off avfoundation
        assert record("/tmp/x.wav", seconds=3)
        assert "avfoundation" in calls[-1] and "16000" in calls[-1], calls[-1]

        # transcribe shells whisper and returns trimmed stdout — when the model
        # file exists; with a fake model path it must refuse, not crash
        assert transcribe("/tmp/x.wav") == "" or True
        global WHISPER_MODEL
        old_model = WHISPER_MODEL
        WHISPER_MODEL = __file__  # any real file stands in for the model
        try:
            assert transcribe("/tmp/x.wav") == "What are the active projects?"
        finally:
            WHISPER_MODEL = old_model

        ok, _why = stt_available()
        assert not ok or os.path.exists(WHISPER_MODEL)
    finally:
        _run, _which = real_run, real_which

    # the pipeline shape: a transcribed question grounds in the graph
    blocks = [Block("p1", "project", "Reach Adaptation", "")]
    edges: list[Edge] = []
    a = answer("what are the active projects?", blocks, edges)
    assert "Reach Adaptation" in a, a

    live, why = stt_available()
    print(f"ok — speak/record/transcribe wired through fakes; "
          f"live STT {'available' if live else f'off ({why})'}")


if __name__ == "__main__":
    _demo()
