# Plan

## North star

> A new lab member gets a link, talks to it for twenty minutes, and afterward can do
> their first week without asking a question a file already answers.
> Nothing identifying ever leaves the lab machine.

Two clauses, both testable. If either fails the product is not interesting: without
the first it's a search box, without the second a clinical lab can't install it.

## Phases

Each phase ends with something a stranger can run and check. No phase depends on the
next one being good.

**0 — contract.** Done. `postdoc.py` defines the three record types, implements
redaction for real, and self-checks that nothing identifying reaches the wire.
*Check:* `python3 postdoc.py`.

**1 — ingest, no models.** docx and pdf to plain text plus a `FileRecord` skeleton:
path, hash, mtime, size, dedupe. Zero intelligence.
*Check:* point it at a real messy folder. It emits a record per file, crashes on
none, and reports how many of the N files are actually distinct documents. That
number alone is a demo — most labs do not know it.

**2 — local extraction.** A small local model fills `doc_type`, `project`, `people`,
`flags`. This is where redaction gets its teeth, because the doc-type gate and the
flags drive it.
*Check:* **leaks at the boundary, not entity F1** — the self-check's assertion,
scaled. Two tiers, because there is no lab data yet. Tier 1, runnable today: a
bootstrap corpus of real open documents — public REB/IRB consent templates,
protocols.io protocols, open-access papers and theses, OpenNeuro dataset docs — with
fake identifiers injected into copies from a generated manifest. Real clinical
formatting is what breaks NER; the manifest makes the gate exact: every manifest
string absent from the wire, no hand-labelling needed. Tier 2, the release gate once
user zero exists: 50 real lab documents, same assertion. Entity F1 and doc_type
agreement are diagnostics, never the gate. Synthetic PII benchmarks do not transfer
(the bootstrap corpus is real formatting with planted spans — a stated compromise,
not a loophole), and a human reviews the first packet from any new lab before it
sends. Report crossing rate alongside leak count — a gate that holds everything back
also "passes". A missed consent form is the failure that ends the project.

**3 — interpretation.** `SafePacket` to blocks and edges, via a frontier model that
only ever sees pseudonyms.
*Check:* show the graph to a PI. They either say "yes, that's my lab" or point at
what's wrong. That's the eval. It doesn't need to be more numerical than that yet.

**4 — walkthrough and voice.** Two-way speech over the approved packet.
*Check:* the pass condition. A real newcomer, after one session, can name the active
projects, say what data exists for theirs and where it lives, name who to ask about
what, and identify the protocol they're working under. Four questions, graded by a
human.

**5 — PI approval.** The gate before anything reaches a newcomer.
*Check:* time it with a stopwatch. Over ten minutes per newcomer and it gets
redesigned into a one-time blessing plus exception review, because a PI will not
spend eleven.

## Stack

Research done, validated pillar by pillar. This is a decision record for phases 1–2,
not an install list — nothing here is a dependency until the phase that needs it
lands.

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

## Risks, honestly

Redaction recall is the entire product, and regex plus model flags will miss things.
The mitigation that works is failing closed and a human reviewing the first send —
not better regex. Resist the urge to believe the patterns are good.

Local model quality on classification is the most likely disappointment. Phase 1
shipping value on its own is the hedge: dedupe and inventory are useful even if
phase 2 is mediocre.

Scope creep has a specific shape here — every cut idea in the README is genuinely
good, which is exactly why they're dangerous.
