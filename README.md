# onboard-lab-member-without-a-postdoc

A digital post-doc for labs. You point it at the lab's files; it works out what the
lab actually does, and walks a new person through it out loud.

Not a chatbot over your Drive. The point is that lab knowledge lives in files nobody
has opened in four years, and it dies when people graduate.

> A new lab member gets a link, talks to it for twenty minutes, and afterward can do
> their first week without asking a question a file already answers.
> Nothing identifying ever leaves the lab machine.

Two clauses, both testable. If either fails the product is not interesting: without
the first it's a search box, without the second a clinical lab can't install it.

## Architecture

Three layers. The split exists because clinical research data cannot leave the
machine, but the reasoning that makes this useful needs a frontier model.

```
  LOCAL (lab machine)                    | PROXY |        ONLINE
  ─────────────────────────────────────────────────────────────────────────
  1. catalogue      walks the fileset,   |       |   3. interpretation
                    docx/pdf -> JSON.    |       |      synthesis, the
                    Spotlight, but it    |       |      onboarding blocks,
                    knows what a         |       |      the conversation,
                    consent form is.     |       |      voice (ElevenLabs)
                                         |       |
  2. redaction      decides what is      |       |   sees pseudonyms only.
                    allowed to cross.    |       |   never sees a name, an
                    fails closed.        |       |   MRN, or a raw file.
                                         |       |
  rehydration       maps pseudonyms back |       |
                    to real names, for   |       |
                    viewers cleared to   |       |
                    see them.            |       |
```

The division of labour matters: small local models are bad at synthesis and fine at
extraction, so extraction is what they do. Synthesis happens online, on data that has
already been stripped.

Redaction is two controls, in order. **Document-class gating** comes first: a
document classified `consent`, or carrying clinical measures, never crosses at all —
only its existence and metadata do. **Entity redaction** comes second: regex plus NER
scrubs what remains. The order is the finding that shaped the design: general-purpose
PII models drop from ~0.81 F1 on general text to ~0.41 on clinical text, so
entity-level ML cannot be what stands between a consent form and the wire. It is the
recall assist; the gate is the control.

**The proxy is the whole product.** If redaction is trustworthy, a clinical lab can
use this. If it isn't, nothing else counts.

## The contract

`postdoc.py` defines three record types. They are the actual design; the rest is
swappable.

- `FileRecord` — layer 1 output. **Local only.** May contain PHI.
- `SafePacket` — what crosses the proxy. Pseudonymised, fails closed.
- `Block` / `Edge` — layer 3 output. The onboarding artifact, as a small graph.

A block is one project, person, protocol, or dataset. An edge says one used,
produced, supervised, or superseded another. The walkthrough is a path through that
graph, narrated.

## Files

| File | What it is |
|---|---|
| `postdoc.py` | The frozen layer contract. The three record types, a real redactor (the trust boundary), stubbed catalogue and interpretation layers, and a self-check that doubles as the repo's only cross-layer test. |
| `ingest.py` | Layer 1, phase 1. Walks a directory tree, dedupes by sha256, emits one `FileRecord` skeleton per distinct document plus a local-only hash-to-paths index. Local only, zero intelligence. |
| `textget.py` | Layer 1, phase 1. File to plain text — docx/txt/md via stdlib, pdf via guarded `pypdf`. Local only; output can hold PHI. |
| `extract.py` | Layer 1, phase 2. Fills `doc_type`/`project`/`people`/`flags` via a local Ollama model, temperature 0, JSON-schema constrained. Fails closed: unreachable, timed-out, or unparseable output gets tagged `unverified_extraction`, which `postdoc.Redactor` doesn't know how to handle and so holds back automatically. |
| `corpus.py` | Validation asset, offline. Fabricates a small deterministic corpus of lab-like documents with known-planted fake identifiers plus a `manifest.json`, for the phase-2 leak gate. |
| `leakgate.py` | Phase-2 validation gate, end to end. Runs the real pipeline (ingest → textget → extract → `Redactor.to_packet`) over the generated corpus and asserts zero manifest strings reach the wire. Offline-runnable — every record fails closed with no local model, so the honest offline crossing rate is 0%. |
| `synthesize.py` | Layer 3, the real interpretation pass. `SafePacket` to `Block`/`Edge` behind a swappable `Backend` — `FakeBackend` (deterministic, no network) or `AnthropicBackend` (live). Refuses anything that isn't a `SafePacket`; validates model output against postdoc's vocab and falls back to `postdoc.interpret` on unparseable output. |
| `graphview.py` | Layer 3 presentation. Pure, deterministic `(list[Block], list[Edge]) -> str` Mermaid rendering — no I/O, no model calls, no randomness. |
| `walkthrough.py` | Layer 3/4 lite. Turns an approved graph into spoken-style narration and answers keyword questions against it. No model, no speech — that's phase 4 proper. |
| `approve.py` | Phase 5, the PI gate. Renders a `SafePacket` exactly as it will cross the wire; `approve()` is the only door, always writes an audit line, and returns the packet on yes / `None` on no. |
| `voice.py` | Phase 4, the speech shell. Composable push-to-talk: whisper.cpp STT → `walkthrough.answer` → macOS `say` TTS, three separate processes so the middle one only ever sees the approved graph. Degrades to typed input when whisper is absent. |
| `cli.py` | The wiring. argparse over the pipeline: `scan`/`extract`/`packet`/`gate`/`graph`/`talk [--voice]`/`leakgate`, offline by default. |
| `README.md` | This file. Architecture, roadmap, stack, validation — the only document. |
| `LICENSE` | Apache-2.0. Permissive like MIT but with an express patent grant, which is what hospital and university legal teams approve most readily. |
| `.gitignore` | The usual Python noise. |

## Scope

**In:** local catalogue of docx/pdf, redaction proxy, tailored onboarding artifact,
two-way speech, PI approval before anything reaches a newcomer, per-project REB flag.

**Cut, deliberately:** virtual scenario testing, a versioning identifier, MATLAB/MR/
clinical-measure ingest, analysis toolkit, the find-more-data incentive. All good
ideas. None of them are the first thing.

## Phases

Each phase ends with something a stranger can run and check. No phase depends on the
next one being good.

**0 — contract.** Done. `postdoc.py` defines the record types, implements redaction
for real, and self-checks that nothing identifying reaches the wire.
*Check:* `python3 postdoc.py`.

**1 — ingest, no models.** Built (`ingest.py`, `textget.py`). docx and pdf to plain text plus a `FileRecord` skeleton:
path, hash, mtime, size, dedupe. Zero intelligence.
*Check:* point it at a real messy folder. It emits a record per file, crashes on
none, and reports how many of the N files are actually distinct documents. That
number alone is a demo — most labs do not know it.

**2 — local extraction.** Built, offline fail-closed by default, live via local Ollama
(`extract.py`, `leakgate.py`, `corpus.py`). A small local model fills `doc_type`, `project`, `people`,
`flags`. This is where redaction gets its teeth, because the doc-type gate and the
flags drive it.
*Check:* **leaks at the boundary, not entity F1** — the self-check's assertion,
scaled. Two tiers, because there is no lab data yet. Tier 1, runnable today: a
bootstrap corpus of real open documents (public REB/IRB consent templates,
protocols.io protocols, open-access papers and theses, OpenNeuro dataset docs) with
fake identifiers injected into copies from a generated manifest. Real clinical
formatting is what breaks NER; the manifest makes the gate exact: every manifest
string absent from the wire, no hand-labelling needed. Tier 2, the release gate once
user zero exists: 50 real lab documents, same assertion. Entity F1 and doc_type
agreement are diagnostics, never the gate. Report crossing rate alongside leak
count — a gate that holds everything back also "passes". A missed consent form is
the failure that ends the project, and a human reviews the first packet from any new
lab before it sends.

**3 — interpretation.** Built, behind `FakeBackend`/`AnthropicBackend`
(`synthesize.py`, `graphview.py`). `SafePacket` to blocks and edges, via a frontier model that
only ever sees pseudonyms.
*Check:* show the graph to a PI. They either say "yes, that's my lab" or point at
what's wrong. That's the eval. It doesn't need to be more numerical than that yet.

**4 — walkthrough and voice.** Built as a lite (`walkthrough.py`, `voice.py`) —
narration and keyword Q&A over an approved graph, spoken aloud on macOS via
`say`, with push-to-talk whisper.cpp STT when installed (`brew install
whisper-cpp` plus a ggml model at `~/.cache/whisper/`). Two-way speech over the
approved packet; the conversational LLM layer on top is phase 4 proper.
*Check:* the pass condition. A real newcomer, after one session, can name the active
projects, say what data exists for theirs and where it lives, name who to ask about
what, and identify the protocol they're working under. Four questions, graded by a
human.

**5 — PI approval.** Built, as `approve.py`. The gate before anything reaches a newcomer.
*Check:* time it with a stopwatch. Over ten minutes per newcomer and it gets
redesigned into a one-time blessing plus exception review, because a PI will not
spend eleven.

## Stack

Research done, validated pillar by pillar. This is a decision record for phases 1–2,
not an install list — nothing here is a dependency until the phase that needs it
lands. Today the repo is stdlib-only.

| Pillar | Choice | Note |
|---|---|---|
| Local extraction | Qwen3.5-4B Q4 GGUF via Ollama | Apache 2.0, CPU-viable. Fallbacks: Phi-4-mini, Granite 3.3 8B |
| Structured output | Constrained decoding — Ollama JSON Schema `format`, GBNF, or Instructor + Pydantic | Makes malformed JSON mechanically impossible |
| PDF routing | `pdfmux` as default router | MIT. Classify → route → audit → re-extract, with confidence scores |
| PDF backends | Docling (tables/structure), Tesseract (scans) | Docling MIT/Apache |
| Redaction chassis | Microsoft Presidio | MIT, regex + pluggable NER, has `GLiNERRecognizer` |
| Redaction NER | NVIDIA GLiNER-PII; evaluate GLiNER2-PII | Newer variant shows better clinical recall. Phase-2 bake-off on real documents — Presidio makes it a swap |
| Interpretation | Cheapest capable hosted tier (Gemini Flash-Lite class) with schema output | Sees `SafePacket` only. Volume is one graph per lab — cost is noise |
| Voice | Composable: Whisper/Parakeet STT → our LLM → Kokoro TTS | ElevenLabs in custom-LLM mode as the fast prototype path. End-to-end speech models are disqualified, not unpreferred: they fuse transcription with reasoning, which makes "only the approved packet crosses" unenforceable |
| Transport | Tailscale, self-hosted Headscale option | No public exposure. Config swap, not architecture |

Do not stuff a thesis into the 262k context. Chunk it — the context length is real
but CPU throughput is the binding constraint.

**Licensing.** PyMuPDF4LLM is AGPL — the best text-layer extractor, but a real
problem for an Apache-2.0 project with commercial intent, and it may sit underneath
`pdfmux` as a routing target. Verify `pdfmux`'s dependency licenses before adopting
it in phase 1; if PyMuPDF is in the tree, take the Docling path and accept slightly
worse text-layer output for a clean license story.

## Validation

Every check is a command with an exit code or an assertion over the serialised
packet — never an opinion. Whoever (or whatever) wrote a diff does not validate it.

The redactor has already survived two adversarial red-team rounds: every confirmed
leak was either fixed and planted into the self-check as a regression string, or
recorded as a named ceiling in a `ponytail:` comment with GLiNER-PII in Presidio as
the phase-2 upgrade path. The residual class — bare dates, unlisted names, short
bare numerics — is exactly what regex cannot do, and is survivable now because the
doc-class gate holds the worst documents back whole. Widening the regex further is
an arms race; resist the urge to believe the patterns are good. The mitigation that
works is failing closed and a human reviewing the first send.

When the open corpus lands, phase 2 validates by manifest: inject known fake
identifiers into real-format documents, run the pipeline, assert zero manifest
strings on the wire, and report crossing rate alongside — a gate that holds
everything back is also broken.

## Risks, honestly

Redaction recall is the entire product, and regex plus model flags will miss things.
The mitigation is the doc-class gate, failing closed, and a human reviewing the
first send — not better regex.

Local model quality on classification is the most likely disappointment. Phase 1
shipping value on its own is the hedge: dedupe and inventory are useful even if
phase 2 is mediocre.

Scope creep has a specific shape here — every cut idea above is genuinely good,
which is exactly why they're dangerous.

## Still open

These are holes, not oversights. Marked so they get answered by building rather than
by arguing.

- **Pass condition.** Current guess: after the walkthrough the newcomer can name the
  active projects, say what data exists for theirs and where it lives, name who to
  ask about what, and identify the protocol their work follows. Testable, which is
  the point. Needs confirming.
- **PI gate.** Guessed as one-time per-lab blessing plus per-newcomer exception
  review. If it turns into a per-newcomer chore, adoption dies in month two.
- **People.** Factual only for now — who ran what, when they overlapped, what they
  left behind. The "what were they like" version is a different feature with a
  different risk profile and is not built.
- **User zero.** A Drive-based lab and a clinical lab want different halves of this.

## Run

```
python3 postdoc.py     # self-check, no dependencies
python3 cli.py          # self-check: offline pipeline end to end, no models, no network
```

The pipeline, offline by default (`extract` and `graph` take `--live` to use a real
local Ollama model / `AnthropicBackend`):

```
python3 cli.py scan ROOT                    # layer 1: walk + textget -> records.json (0600, PHI)
python3 cli.py extract records.json         # fill fields; offline fails every record closed
python3 cli.py packet records.json          # the proxy: redact -> packet.json (pseudonyms only)
python3 cli.py gate packet.json             # PI approval gate, logs a decision (--yes to skip prompt)
python3 cli.py graph packet.json            # layer 3: synthesize + render the onboarding graph
python3 cli.py talk packet.json             # narrate the graph, answer questions
python3 cli.py leakgate                     # phase-2 validation: generate corpus, assert no leak crosses
```
