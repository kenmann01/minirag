# Internal and Confidential - Not for External Distribution.
"""Convert public PDFs into markdown with numbered section headings."""

import re
from pathlib import Path

_EFFECTIVE_DATE = re.compile(
    r"(?:Effective(?:\s+Date)?|Dated)\s*[:\-]?\s*"
    r"(\d{4}-\d{2}-\d{2}|[A-Z][a-z]+ \d{1,2}, \d{4})",
    re.IGNORECASE,
)
_CFR_SECTION = re.compile(r"^§\s*\d+\.(\d+)(?:\s+(.+))?$")
_DASHED_SECTION = re.compile(r"^(\d+)-(\d+)$")
_NAMED_SECTION = re.compile(
    r"^(?:Section|Chapter)\s+(\d+)(?:\.\d+)*\.?\s+(.+)$",
    re.IGNORECASE,
)
_NUMBERED_HEADING = re.compile(r"^(\d+)\.\s+(\S.*)$")
_MAX_TITLE_CHARS = 120


def extract_text(pdf_path: Path) -> str:
    """Read a PDF into plain text, one page after another."""
    import fitz

    document = fitz.open(pdf_path)
    try:
        return "\n".join(page.get_text() for page in document)
    finally:
        document.close()


def _clean(line: str) -> str:
    return " ".join(line.strip().split())


def _title_text(text: str) -> str | None:
    if not text or len(text) > _MAX_TITLE_CHARS:
        return None
    words = text.split()
    if not words or len(words) > 12 or text.endswith("."):
        return None
    if not text[:1].isupper() or text.lower().startswith("figure"):
        return None
    return text.strip(" .")


def _heading(line: str) -> tuple[str, str] | None:
    text = _clean(line)
    if not text or len(text) > _MAX_TITLE_CHARS:
        return None
    cfr = _CFR_SECTION.match(text)
    if cfr and cfr.group(2):
        title = _title_text(cfr.group(2).strip()) or f"Section {cfr.group(1)}"
        return cfr.group(1), title
    named = _NAMED_SECTION.match(text)
    if named:
        return named.group(1), named.group(2).strip(" .")
    numbered = _NUMBERED_HEADING.match(text)
    if numbered:
        title = numbered.group(2).strip()
        if len(title.split()) > 6 or any(mark in title for mark in "?.:;"):
            return None
        return numbered.group(1), title.strip(" .")
    return None


def _marker(line: str) -> str | None:
    """A section number that may be waiting for its title on the next line."""
    text = _clean(line)
    dashed = _DASHED_SECTION.match(text)
    if dashed:
        return dashed.group(2)
    cfr = _CFR_SECTION.match(text)
    if cfr and not cfr.group(2):
        return cfr.group(1)
    return None


def _title_on_next(lines: list[str], index: int) -> str | None:
    follower = index + 1
    while follower < len(lines) and not _clean(lines[follower]):
        follower += 1
    if follower >= len(lines):
        return None
    return _title_text(_clean(lines[follower]))


def to_markdown(text: str) -> str | None:
    """Turn extracted PDF text into heading-structured markdown.

    Returns:
        Markdown whose sections use ``## N. Title``, or None when the
        document yields zero sections. An effective date is kept when the
        source states one.
    """
    text = re.sub(r"-\n([a-z])", r"\1", text)
    date_match = _EFFECTIVE_DATE.search(text)
    sections: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        marker = _marker(lines[index])
        if marker is not None and (current is None or current[0] != marker):
            title = _title_on_next(lines, index)
            if current is not None:
                sections.append(current)
            current = (marker, title or f"Section {marker}", [])
            if title:
                follower = index + 1
                while follower < len(lines) and not _clean(lines[follower]):
                    follower += 1
                index = follower + 1
            else:
                index += 1
            continue
        if marker is not None:
            index += 1
            continue
        heading = _heading(lines[index])
        if heading is not None and (current is None or current[0] != heading[0]):
            if current is not None:
                sections.append(current)
            current = (heading[0], heading[1], [])
            index += 1
            continue
        if current is not None:
            current[2].append(lines[index].rstrip())
        index += 1
    if current is not None:
        sections.append(current)
    if not sections:
        return None
    parts: list[str] = []
    if date_match:
        parts.append(f"**Effective Date:** {date_match.group(1).strip()}")
        parts.append("")
    for number, title, body_lines in sections:
        parts.append(f"## {number}. {title}")
        parts.append("")
        body = "\n".join(body_lines).strip()
        if body:
            parts.append(body)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def convert_document(pdf_path: Path) -> str | None:
    """Convert one PDF. None means the document yielded zero sections."""
    return to_markdown(extract_text(pdf_path))


def convert_dir(pdf_dir: Path, out_dir: Path) -> int:
    """Write one markdown file per PDF.

    A document that yields zero sections is not written. The process status
    is nonzero when any document fails or the folder has no PDFs.

    Args:
        pdf_dir: Folder of source PDFs.
        out_dir: Folder that receives ``.md`` files.

    Returns:
        Zero when every PDF produced at least one section, else one.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(path for path in pdf_dir.glob("*.pdf") if path.is_file())
    if not pdfs:
        return 1
    failed = False
    for pdf_path in pdfs:
        markdown = convert_document(pdf_path)
        destination = out_dir / f"{pdf_path.stem}.md"
        if markdown is None:
            failed = True
            if destination.exists():
                destination.unlink()
            continue
        destination.write_text(markdown, encoding="utf-8")
    return 1 if failed else 0
