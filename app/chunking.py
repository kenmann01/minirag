# Internal and Confidential — Not for External Distribution.
"""Split the versioned expense policy into section-level chunks."""

import re

_HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)
_TITLE = re.compile(r"^# (.+?) — Version (.+)$", re.MULTILINE)


def split(markdown: str) -> list[dict]:
    """Split a versioned expense policy into one record per numbered section.

    Args:
        markdown: Complete policy text with a versioned title and numbered
            second-level headings.

    Returns:
        Chunk dictionaries containing policy metadata and section text.

    Raises:
        ValueError: If the policy has no title containing its version.
    """
    title_match = _TITLE.search(markdown)
    if title_match is None:
        raise ValueError("policy markdown is missing a versioned title")
    document = title_match.group(1).strip()
    version = title_match.group(2).strip()

    headings = list(_HEADING.finditer(markdown))
    chunks: list[dict] = []
    for index, heading in enumerate(headings):
        body_start = heading.end()
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        section = heading.group(1)
        section_title = heading.group(2)
        chunks.append(
            {
                "chunk_id": f"expense-policy:v{version}:section-{section}",
                "document": document,
                "version": version,
                "section": section,
                "section_title": section_title,
                "text": markdown[body_start:body_end].strip(),
            }
        )
    return chunks
