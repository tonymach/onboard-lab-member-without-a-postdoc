"""synthesize — layer 3, the real interpretation pass. See README.md phase 3.

Consumes a SafePacket (pseudonyms only) and returns the onboarding graph as
postdoc.Block / postdoc.Edge. This module is the *online* side of the proxy:
it must be structurally incapable of touching a raw FileRecord, so the first
thing synthesize does is refuse anything that is not a SafePacket.

The model is swappable behind the Backend protocol. FakeBackend is the
deterministic, network-free path the self-check and CI run on; AnthropicBackend
is the one hosted implementation. Whatever the model returns is untrusted text
— every block kind and edge rel is validated against postdoc's vocab, bad rows
are dropped, and wholly unparseable output falls back to postdoc.interpret so
the pipeline degrades instead of breaking.

    python3 synthesize.py    # self-check, no network, no model
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import asdict
from typing import Protocol

import postdoc

# The packet JSON rides at the tail of the prompt after this marker. A real
# model just reads it as context; FakeBackend parses it back out to build a
# deterministic graph without pretending to reason.
_MARKER = "PACKET_JSON:"

_INSTRUCTIONS = f"""You turn a redacted research-lab packet into a knowledge graph.
Return ONLY strict JSON, no prose, of the form:
  {{"blocks": [{{"id": str, "kind": str, "label": str, "detail": str}}],
   "edges": [{{"src": block_id, "dst": block_id, "rel": str}}]}}
block.kind must be one of {list(postdoc.BLOCK_KINDS)}.
edge.rel must be one of {list(postdoc.EDGE_RELS)}.
Every edge src/dst must be an id that appears in blocks.
The packet follows the marker line below."""


class Backend(Protocol):
    def complete(self, prompt: str) -> str: ...


def _prompt(packet: postdoc.SafePacket) -> str:
    return f"{_INSTRUCTIONS}\n{_MARKER}\n{json.dumps(asdict(packet))}"


def _packet_from_prompt(prompt: str) -> postdoc.SafePacket:
    raw = json.loads(prompt.split(_MARKER, 1)[1])
    return postdoc.SafePacket(
        records=[postdoc.SafeRecord(**r) for r in raw["records"]],
        held_back=raw["held_back"],
    )


class FakeBackend:
    """Deterministic, no network. Reproduces postdoc.interpret and adds a
    protocol layer, so the online contract is exercised end-to-end offline."""

    def complete(self, prompt: str) -> str:
        packet = _packet_from_prompt(prompt)
        blocks, edges = postdoc.interpret(packet)  # project + person blocks, produced edges
        seen = {b.id for b in blocks}
        # Superset: protocol records become their own block, wired to their project.
        for r in packet.records:
            if r.doc_type == "protocol" and r.ref not in seen:
                blocks.append(postdoc.Block(r.ref, "protocol", r.title or r.ref, r.summary))
                seen.add(r.ref)
                if r.project:
                    edges.append(postdoc.Edge(r.project, r.ref, "used"))
        return json.dumps({"blocks": [asdict(b) for b in blocks],
                           "edges": [asdict(e) for e in edges]})


class AnthropicBackend:
    """One hosted backend, stdlib urllib only.
    ponytail: Gemini Flash-Lite class (README stack table) is a drop-in swap —
    same Backend.complete contract, different URL/headers/response shape. Add a
    second class when a lab needs a cheaper tier; do not abstract for one impl."""

    URL = "https://api.anthropic.com/v1/messages"
    MODEL = "claude-haiku-4-5-20251001"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ["ANTHROPIC_API_KEY"]

    def complete(self, prompt: str) -> str:
        body = json.dumps({
            "model": self.MODEL,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(self.URL, data=body, headers={
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
        return "".join(part.get("text", "") for part in data["content"])


def _validate(raw: str) -> tuple[list[postdoc.Block], list[postdoc.Edge]] | None:
    """Parse model JSON into validated Block/Edge. None => wholly unparseable."""
    try:
        obj = json.loads(raw)
        block_rows, edge_rows = obj["blocks"], obj["edges"]
    except (json.JSONDecodeError, TypeError, KeyError):
        return None

    blocks, dropped_b = [], 0
    for b in block_rows:
        try:
            if b["kind"] in postdoc.BLOCK_KINDS:
                blocks.append(postdoc.Block(str(b["id"]), b["kind"],
                                            b.get("label", ""), b.get("detail", "")))
                continue
        except (TypeError, KeyError):
            pass
        dropped_b += 1

    ids = {b.id for b in blocks}
    edges, dropped_e = [], 0
    for e in edge_rows:
        try:
            if e["rel"] in postdoc.EDGE_RELS and e["src"] in ids and e["dst"] in ids:
                edges.append(postdoc.Edge(e["src"], e["dst"], e["rel"]))
                continue
        except (TypeError, KeyError):
            pass
        dropped_e += 1

    if dropped_b or dropped_e:
        print(f"synthesize: dropped {dropped_b} blocks, {dropped_e} edges "
              "(off-vocab or dangling)", file=sys.stderr)
    return blocks, edges


def synthesize(packet: postdoc.SafePacket,
               backend: Backend) -> tuple[list[postdoc.Block], list[postdoc.Edge]]:
    # Hard boundary: this module never sees a raw record. A FileRecord or a dict
    # (layer-1 output) must not reach a hosted model — fail loudly, not silently.
    if not isinstance(packet, postdoc.SafePacket):
        raise TypeError(f"synthesize needs a SafePacket, got {type(packet).__name__}")

    result = _validate(backend.complete(_prompt(packet)))
    if result is None:
        # ponytail: degraded beats broken — the stubbed interpreter always yields
        # a valid (if shallow) graph. Ceiling: no retry/repair of malformed JSON;
        # add a one-shot "return valid JSON only" reprompt if hosted output is flaky.
        print("synthesize: unparseable model output, falling back to interpret",
              file=sys.stderr)
        return postdoc.interpret(packet)
    return result


# ─── self-check ──────────────────────────────────────────────────────────────

class _Canned:
    """Backend that returns a fixed string, for exercising the validator."""
    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, prompt: str) -> str:
        return self.text


def _demo() -> None:
    packet = postdoc.SafePacket(
        records=[
            postdoc.SafeRecord("aaaaaaaaaaaa", "thesis", "PERSON_01 thesis",
                               ["PERSON_01"], "reach-adaptation", None, None),
            postdoc.SafeRecord("bbbbbbbbbbbb", "protocol", "reach block protocol",
                               ["PERSON_01"], "reach-adaptation", "Reach protocol", None),
        ],
        held_back=[],
    )

    # 1. FakeBackend end-to-end: superset of interpret (project + person + protocol).
    blocks, edges = synthesize(packet, FakeBackend())
    kinds = {b.kind for b in blocks}
    assert kinds == {"project", "person", "protocol"}, kinds
    ids = {b.id for b in blocks}
    assert all(e.src in ids and e.dst in ids for e in edges)
    assert postdoc.Edge("PERSON_01", "reach-adaptation", "produced") in edges
    assert postdoc.Edge("reach-adaptation", "bbbbbbbbbbbb", "used") in edges

    # 2. Vocab validation drops a poisoned block and its dangling edge, keeps the good one.
    poisoned = _Canned(json.dumps({
        "blocks": [
            {"id": "p", "kind": "project", "label": "reach", "detail": ""},
            {"id": "evil", "kind": "malware", "label": "x", "detail": ""},  # off-vocab kind
        ],
        "edges": [
            {"src": "evil", "dst": "p", "rel": "used"},       # dangling endpoint -> dropped
            {"src": "p", "dst": "p", "rel": "hacked"},        # off-vocab rel -> dropped
        ],
    }))
    blocks, edges = synthesize(packet, poisoned)
    assert [b.id for b in blocks] == ["p"], blocks  # poison block gone
    assert edges == [], edges                       # both edges dropped

    # 3. Wholly unparseable output falls back to interpret, still a valid graph.
    blocks, edges = synthesize(packet, _Canned("not json at all"))
    assert blocks and {b.kind for b in blocks} <= set(postdoc.BLOCK_KINDS)

    # 4. Hard boundary: a raw dict (or FileRecord) can never reach the model.
    for raw in ({"records": []}, postdoc.FileRecord("/p", "z" * 64, "notes", "hi")):
        try:
            synthesize(raw, FakeBackend())
            assert False, "raw record crossed the boundary"
        except TypeError:
            pass

    print(f"ok — FakeBackend graph: {len(blocks)} blocks; validator drops off-vocab; "
          "raw records raise TypeError")


if __name__ == "__main__":
    _demo()
