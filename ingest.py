"""ingest — layer 1, phase 1: the walker. Local only, zero intelligence.

Walks a directory tree and produces one FileRecord skeleton per distinct
document (deduped by sha256), plus a local-only index that maps each hash
back to every path that carries it. The index never crosses the wire —
FileRecord has no mtime/size fields, so it can't; the index is the sidecar
that phase 2's model loop and any future "which copy is canonical" logic
reads from.

Gate for this phase is "crashes on none": every per-file failure is caught
and counted, never raised.

    python3 ingest.py    # self-check
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from postdoc import FileRecord

SKIP_DIRS = {".git", "__pycache__"}
MAX_BYTES = 50 * 1024 * 1024  # ponytail: hard size ceiling, no chunked/streaming ingest for huge files yet — raise or stream when a lab has one
HASH_CHUNK = 1 << 20


@dataclass
class IngestResult:
    records: list[FileRecord]
    index: dict[str, dict]  # sha256 -> {"paths": [...], "mtime": float, "size": int} — local sidecar, never serialised out
    files_seen: int = 0  # successfully hashed (post skip-filter)
    unreadable: int = 0


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest(root: str) -> IngestResult:
    result = IngestResult(records=[], index={})
    for dirpath, dirnames, filenames in os.walk(root):
        # prune in place so os.walk never descends into skipped dirs
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
                if st.st_size > MAX_BYTES:
                    continue  # ponytail: silently dropped, not counted as an error — it's a policy skip, not a failure
                sha = _hash_file(path)
            except OSError:
                result.unreadable += 1
                continue

            result.files_seen += 1
            if sha in result.index:
                result.index[sha]["paths"].append(path)
                continue
            result.index[sha] = {"paths": [path], "mtime": st.st_mtime, "size": st.st_size}
            result.records.append(
                FileRecord(
                    path=path,
                    sha256=sha,
                    doc_type="unknown",
                    summary="",
                    people=[],
                    flags=[],
                )
            )
    return result


def stats(result: IngestResult) -> str:
    return f"{result.files_seen} files, {len(result.records)} distinct documents, {result.unreadable} unreadable"


# ─── self-check ──────────────────────────────────────────────────────────────

def _demo() -> None:
    import shutil
    import tempfile

    root = tempfile.mkdtemp(prefix="ingest_demo_", dir="/private/tmp/claude-501/-Users-anthonymachula-code-career-ops/e88abacf-71b5-448a-b488-e36d3d4fbb38/scratchpad")
    locked_path = os.path.join(root, "locked.txt")
    try:
        os.makedirs(os.path.join(root, "sub", ".git", "objects"), exist_ok=True)
        os.makedirs(os.path.join(root, "sub", "__pycache__"), exist_ok=True)

        with open(os.path.join(root, "a.txt"), "w") as f:
            f.write("same content")
        with open(os.path.join(root, "sub", "b.txt"), "w") as f:
            f.write("same content")  # duplicate of a.txt -> one FileRecord, two paths
        with open(os.path.join(root, "c.txt"), "w") as f:
            f.write("different content")
        with open(os.path.join(root, ".hidden"), "w") as f:
            f.write("skip me")
        with open(os.path.join(root, "sub", ".git", "objects", "junk"), "w") as f:
            f.write("skip me too")
        with open(os.path.join(root, "sub", "__pycache__", "junk.pyc"), "w") as f:
            f.write("skip me three")

        with open(locked_path, "w") as f:
            f.write("nope")
        os.chmod(locked_path, 0o000)

        result = ingest(root)

        # locked.txt stats fine but fails to open -> unreadable, not files_seen
        assert result.files_seen == 3, result.files_seen  # a.txt, b.txt, c.txt
        assert result.unreadable == 1, result.unreadable
        assert len(result.records) == 2, result.records  # a/b dedupe to one, c is distinct

        dup_sha = next(sha for sha, v in result.index.items() if len(v["paths"]) == 2)
        assert sorted(os.path.basename(p) for p in result.index[dup_sha]["paths"]) == ["a.txt", "b.txt"]

        # hidden file, .git, __pycache__ never touched the walker at all
        seen_paths = {p for v in result.index.values() for p in v["paths"]}
        assert not any(".hidden" in p or ".git" in p or "__pycache__" in p for p in seen_paths)

        for r in result.records:
            assert r.doc_type == "unknown" and r.summary == "" and r.people == [] and r.flags == []

        line = stats(result)
        assert line == "3 files, 2 distinct documents, 1 unreadable", line

        print(f"ok — {line}")
    finally:
        os.chmod(locked_path, 0o644)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    _demo()
