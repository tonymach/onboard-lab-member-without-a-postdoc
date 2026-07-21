# onboard-lab-member-without-a-postdoc

A digital post-doc for labs. You point it at the lab's files; it works out what the
lab actually does, and walks a new person through it out loud.

Not a chatbot over your Drive. The point is that lab knowledge lives in files nobody
has opened in four years, and it dies when people graduate.

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

## Scope

**In:** local catalogue of docx/pdf, redaction proxy, tailored onboarding artifact,
two-way speech, PI approval before anything reaches a newcomer, per-project REB flag.

**Cut, deliberately:** virtual scenario testing, a versioning identifier, MATLAB/MR/
clinical-measure ingest, analysis toolkit, the find-more-data incentive. All good
ideas. None of them are the first thing.

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
```
