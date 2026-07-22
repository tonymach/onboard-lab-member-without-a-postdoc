"""extract — layer 1, phase 2: local structured extraction.

Fills a FileRecord's doc_type/project/title/date/people/flags from its text,
using a small local model over Ollama. Local only; input and output may hold PHI.

Two stages over (record, text):
  1. preflag(text)          — regex recall assist, reuses postdoc.PATTERNS. Pure,
                              always runs, cannot fail. Its flags are the floor.
  2. extract(record, text)  — Ollama structured extraction, temperature 0, JSON
                              schema constrained. Merges its output over preflag.

FAIL CLOSED is the whole point. If Ollama is unreachable, times out (30s), or
returns anything unparseable, extract returns the record carrying its preflag
flags PLUS "unverified_extraction". That flag is deliberately NOT in
postdoc.HANDLED_FLAGS, so postdoc.Redactor holds the record back automatically —
an unverified record never crosses the proxy. Silence from the model is treated
as PHI, not as an empty document.

    python3 extract.py    # self-check, no Ollama, no network
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import replace
from typing import Callable

from postdoc import DOC_TYPES, HANDLED_FLAGS, PATTERNS, FileRecord

OLLAMA_URL = "http://localhost:11434"
TIMEOUT = 30  # seconds; a slow model is a dead model as far as fail-closed cares.
CHUNK = 8000  # ~8k chars, first chunk only for v1.

# postdoc.PATTERNS key -> the flag it stands for. Several IDs collapse to "mrn".
_FLAG_OF = {
    "mrn": "mrn", "alnum_id": "mrn", "long_id": "mrn",
    "email": "email", "phone": "phone", "dob": "dob",
    "honorific": "person_name",
}

# A transport is (method, url, payload|None) -> parsed-json dict. Injectable so
# the self-check runs with zero Ollama; the default hits real urllib.
Transport = Callable[[str, str, dict | None], dict]


def _http(method: str, url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def preflag(text: str) -> list[str]:
    """Regex recall assist. Returns the subset of HANDLED_FLAGS the patterns hit."""
    hits: list[str] = []
    for key, pat in PATTERNS.items():
        if pat.search(text):
            flag = _FLAG_OF[key]
            if flag not in hits:
                hits.append(flag)
    return hits


def _pick_model(transport: Transport, base_url: str) -> str:
    env = os.environ.get("POSTDOC_MODEL")
    if env:
        return env
    names = [m["name"] for m in transport("GET", base_url + "/api/tags", None)["models"]]
    if "llama3.1:8b" in names:
        return "llama3.1:8b"
    # ponytail: naive fallback — first non-qwen tag (qwen2.5:32b is present but too
    # slow to default to). Proper capability ranking is a phase-2 concern.
    for n in names:
        if "qwen" not in n:
            return n
    return names[0]  # only qwen left; accept the slowness rather than fail.


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
            "project": {"type": "string"},
            "title": {"type": "string"},
            "date": {"type": "string"},
            "people": {"type": "array", "items": {"type": "string"}},
            "flags": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(HANDLED_FLAGS)},
            },
        },
        "required": ["doc_type", "people", "flags"],
    }


def extract(
    record: FileRecord,
    text: str,
    *,
    base_url: str = OLLAMA_URL,
    transport: Transport = _http,
) -> FileRecord:
    """Fill record from text via Ollama. Fails closed to 'unverified_extraction'."""
    floor = preflag(text)
    try:
        model = _pick_model(transport, base_url)
        payload = {
            "model": model,
            "stream": False,
            "format": _schema(),
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content":
                    "Extract document metadata. Return only the schema fields."},
                # ponytail: first chunk only. Chunk-merge across a whole thesis is
                # phase-2 proper; CPU throughput is the binding constraint anyway.
                {"role": "user", "content": text[:CHUNK]},
            ],
        }
        out = json.loads(transport("POST", base_url + "/api/chat", payload)["message"]["content"])

        doc_type = out.get("doc_type")
        if doc_type not in DOC_TYPES:
            doc_type = "unknown"
        model_flags = [f for f in out.get("flags", []) if f in HANDLED_FLAGS]
        flags = floor + [f for f in model_flags if f not in floor]  # UNION, floor first.

        return replace(
            record,
            doc_type=doc_type,
            project=(out.get("project") or None),
            title=(out.get("title") or None),
            date=(out.get("date") or None),
            people=list(out.get("people", [])),
            flags=flags,
        )
    except Exception:
        # Unreachable, timeout, bad JSON, missing keys — all one thing: unverified.
        # No handler for this flag exists, so the redactor holds the record back.
        return replace(record, flags=floor + ["unverified_extraction"])


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    text = (
        "Jane Chen's thesis on visuomotor adaptation. Contact jane@uni.ca, "
        "reviewed by Dr. Ramirez. MRN 4820193, DOB born 1994-03-02."
    )

    # preflag alone: email, dob, person_name (honorific), mrn — all in HANDLED_FLAGS.
    pf = set(preflag(text))
    assert pf == {"email", "dob", "person_name", "mrn"}, pf
    assert pf <= HANDLED_FLAGS

    skeleton = FileRecord(path="/x/thesis.pdf", sha256="b" * 64, doc_type="unknown", summary="")

    # Happy path: injected transport, no Ollama. Model output unions over preflag.
    def fake(method: str, url: str, payload: dict | None = None) -> dict:
        if url.endswith("/api/tags"):
            return {"models": [{"name": "llama3.1:8b"}]}
        if url.endswith("/api/chat"):
            assert payload["options"]["temperature"] == 0
            assert payload["format"]["properties"]["doc_type"]["enum"] == list(DOC_TYPES)
            return {"message": {"content": json.dumps({
                "doc_type": "thesis",
                "project": "reach-adaptation",
                "title": "Visuomotor adaptation",
                "date": "2024",
                "people": ["Jane Chen"],
                "flags": ["person_name", "phone"],  # phone is model-only, not in preflag
            })}}
        raise AssertionError(url)

    got = extract(skeleton, text, transport=fake)
    assert got.doc_type == "thesis", got
    assert got.project == "reach-adaptation" and got.people == ["Jane Chen"], got
    # Union: preflag floor + model's extra "phone", no "unverified_extraction".
    assert set(got.flags) == pf | {"phone"}, got.flags
    assert "unverified_extraction" not in got.flags
    assert set(got.flags) <= HANDLED_FLAGS

    # doc_type outside DOC_TYPES collapses to "unknown".
    def fake_bad_type(method: str, url: str, payload: dict | None = None) -> dict:
        if url.endswith("/api/tags"):
            return {"models": [{"name": "llama3.1:8b"}]}
        return {"message": {"content": json.dumps(
            {"doc_type": "invoice", "people": [], "flags": []})}}

    assert extract(skeleton, text, transport=fake_bad_type).doc_type == "unknown"

    # Fail closed: real transport at a dead port -> connection refused -> unverified.
    dead = extract(skeleton, text, base_url="http://localhost:1")
    assert "unverified_extraction" in dead.flags, dead
    assert set(preflag(text)) <= set(dead.flags), dead  # floor preserved
    assert "unverified_extraction" not in HANDLED_FLAGS  # redactor will hold it back

    # Fail closed on garbage output too, not just an unreachable host.
    def fake_garbage(method: str, url: str, payload: dict | None = None) -> dict:
        if url.endswith("/api/tags"):
            return {"models": [{"name": "llama3.1:8b"}]}
        return {"message": {"content": "not json at all"}}

    assert "unverified_extraction" in extract(skeleton, text, transport=fake_garbage).flags

    print(f"ok — preflag {sorted(pf)}; extract union {sorted(got.flags)}; "
          f"fail-closed -> unverified_extraction, held back by redactor")


if __name__ == "__main__":
    _demo()
