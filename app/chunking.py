# Internal and Confidential - Not for External Distribution.
"""Parent sections and child windows for policy retrieval.

Child windows target 800-1200 characters with 120-150 characters of overlap.
A flat 500 character window with 50 characters of overlap shatters a section's
rules into arbitrary pieces. One vector for a whole long section blurs those
rules together. Small-to-big chunking keeps a crisp child embedding for search
and the whole parent section for generation, because the answer prompt requires
every condition of the rule.
"""

import re
from pathlib import Path

_HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)
_EFFECTIVE_DATE = re.compile(r"^\*\*Effective Date:\*\*\s*(.+)$", re.MULTILINE)
_SUPERSEDED_BY = re.compile(r"^\*\*Superseded-By:\*\*\s*(.+)$", re.MULTILINE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")

_WINDOW_MIN = 800
_WINDOW_TARGET = 1000
_WINDOW_MAX = 1200
_OVERLAP_MIN = 120


def _sentence_spans(body: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_BREAK.finditer(body):
        spans.append((start, match.start()))
        start = match.end()
    if start < len(body):
        spans.append((start, len(body)))
    return [(span_start, span_end) for span_start, span_end in spans if body[span_start:span_end].strip()]


def _span_length(body: str, spans: list[tuple[int, int]], start: int, end: int) -> int:
    if start >= end:
        return 0
    return len(body[spans[start][0] : spans[end - 1][1]].strip())


def _child_windows(body: str) -> list[str]:
    """Grow sentence-span windows into 800-1200 character child chunks.

    Windows stop growing at the target size and always overlap the next
    window by at least ``_OVERLAP_MIN`` characters so a rule split across
    the boundary stays retrievable from either side.
    """
    spans = _sentence_spans(body)
    if not spans:
        return []
    if _span_length(body, spans, 0, len(spans)) <= _WINDOW_MAX:
        return [body.strip()]

    windows: list[str] = []
    start = 0
    while start < len(spans):
        end = start + 1
        while end < len(spans):
            current = _span_length(body, spans, start, end)
            proposed = _span_length(body, spans, start, end + 1)
            if proposed > _WINDOW_MAX and current >= _WINDOW_MIN:
                break
            if current >= _WINDOW_MIN and proposed > _WINDOW_TARGET:
                break
            if proposed > _WINDOW_MAX:
                break
            end += 1
        windows.append(body[spans[start][0] : spans[end - 1][1]].strip())
        if end >= len(spans):
            break
        next_start = end - 1
        while next_start > start and _span_length(body, spans, next_start, end) < _OVERLAP_MIN:
            next_start -= 1
        if next_start <= start:
            next_start = start + 1
        start = next_start
    return windows


def split(markdown: str, source_doc: str) -> list[dict]:
    """Split one policy document into parent sections and child windows.

    Args:
        markdown: Complete policy text with numbered second-level headings.
        source_doc: File name of the source policy, used in chunk identifiers.

    Returns:
        Chunk dictionaries holding section metadata, the full parent text,
        and one child window per chunk.
    """
    effective = _EFFECTIVE_DATE.search(markdown)
    superseded = _SUPERSEDED_BY.search(markdown)
    effective_date = effective.group(1).strip() if effective else None
    superseded_by = superseded.group(1).strip() if superseded else None
    stem = Path(source_doc).stem

    headings = list(_HEADING.finditer(markdown))
    chunks: list[dict] = []
    for index, heading in enumerate(headings):
        body_start = heading.end()
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        section = heading.group(1)
        body = markdown[body_start:body_end].strip()
        for child_index, window in enumerate(_child_windows(body), start=1):
            chunks.append(
                {
                    "chunk_id": f"{stem}:s{section}:c{child_index:02d}",
                    "source_doc": source_doc,
                    "section": section,
                    "section_title": heading.group(2).strip(),
                    "effective_date": effective_date,
                    "superseded_by": superseded_by,
                    "parent_text": body,
                    "text": window,
                }
            )
    return chunks
