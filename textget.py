"""textget — layer 1, phase 1: file to plain text. See README.md.

Local only. Output text can contain PHI; nothing here talks to the network.
docx and txt/md need nothing beyond stdlib. pdf needs pypdf, guarded — this
machine has it, but a lab machine that doesn't should degrade to "error", not
crash the catalogue walk.

    python3 textget.py    # self-check
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# docx body text lives in word/document.xml as a flat run of w:t nodes inside
# w:p paragraphs. Runs get newline-joined per paragraph; that's the lazy
# correct read — python-docx pulls in a whole package for the same XPath.
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A pdf page below this char count is treated as a scan, not sparse prose.
# ponytail: fixed threshold, not per-page-density-aware. Tesseract OCR is the
# real fix for scans; not built here, this just routes them to needs_ocr.
_OCR_CHAR_THRESHOLD = 20

TEXT_EXTS = frozenset({".txt", ".md"})


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    paragraphs = []
    for p in root.iter(f"{_W_NS}p"):
        runs = [t.text or "" for t in p.iter(f"{_W_NS}t")]
        paragraphs.append("".join(runs))
    return "\n".join(paragraphs)


def _extract_pdf(path: Path) -> tuple[str, str]:
    try:
        import pypdf
    except ImportError:
        return "", "error"
    try:
        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return "", "error"
    text = "\n".join(pages)
    if len(text.strip()) < _OCR_CHAR_THRESHOLD:
        return "", "needs_ocr"
    return text, "ok"


def extract_text(path: str) -> tuple[str, str]:
    """File on disk -> (text, status). Never raises; a bad file is ("", "error")."""
    p = Path(path)
    try:
        if not p.is_file():
            return "", "error"
        suffix = p.suffix.lower()
        if suffix == ".docx":
            return _extract_docx(p), "ok"
        if suffix == ".pdf":
            return _extract_pdf(p)
        if suffix in TEXT_EXTS:
            return p.read_text(encoding="utf-8", errors="replace"), "ok"
        return "", "unsupported"
    except Exception:
        # Fail closed on the read, not on the caller. A corrupt docx/zip must
        # not take down a catalogue walk over a thousand files.
        return "", "error"


def _demo() -> None:
    scratch = Path(
        "/private/tmp/claude-501/-Users-anthonymachula-code-career-ops/"
        "e88abacf-71b5-448a-b488-e36d3d4fbb38/scratchpad"
    )
    scratch.mkdir(parents=True, exist_ok=True)

    # --- a real minimal docx, built by hand via zipfile ---
    docx_path = scratch / "demo.docx"
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t> lab.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Second paragraph.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
    text, status = extract_text(str(docx_path))
    assert status == "ok", status
    assert text == "Hello lab.\nSecond paragraph.", repr(text)

    # --- .txt ---
    txt_path = scratch / "demo.txt"
    txt_path.write_text("plain text file\n", encoding="utf-8")
    text, status = extract_text(str(txt_path))
    assert status == "ok" and text == "plain text file\n", (status, text)

    # --- .md, treated the same as .txt ---
    md_path = scratch / "demo.md"
    md_path.write_text("# heading\n", encoding="utf-8")
    text, status = extract_text(str(md_path))
    assert status == "ok" and "heading" in text, (status, text)

    # --- unsupported extension ---
    other_path = scratch / "demo.xyz"
    other_path.write_text("nope", encoding="utf-8")
    text, status = extract_text(str(other_path))
    assert status == "unsupported" and text == "", (status, text)

    # --- missing file: no exception, status "error" ---
    text, status = extract_text(str(scratch / "does-not-exist.docx"))
    assert status == "error" and text == "", (status, text)

    # --- corrupt docx (not actually a zip): status "error", not a crash ---
    bad_docx = scratch / "corrupt.docx"
    bad_docx.write_bytes(b"not a zip file")
    text, status = extract_text(str(bad_docx))
    assert status == "error" and text == "", (status, text)

    # --- pdf: only run the real path if pypdf is importable; either way the
    # missing-dependency guard must not raise. ---
    try:
        import pypdf  # noqa: F401

        have_pypdf = True
    except ImportError:
        have_pypdf = False

    if have_pypdf:
        from pypdf import PdfWriter

        pdf_path = scratch / "demo.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)  # no text -> scan-like
        with open(pdf_path, "wb") as f:
            writer.write(f)
        text, status = extract_text(str(pdf_path))
        assert status == "needs_ocr" and text == "", (status, text)
        print("     pdf path exercised (blank page -> needs_ocr)")
    else:
        print("     pypdf not installed — skipped live pdf path, guard only")

    print(f"ok — docx/txt/md/unsupported/missing/corrupt all returned correct statuses")


if __name__ == "__main__":
    _demo()
