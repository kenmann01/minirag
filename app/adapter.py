# Internal and Confidential - Not for External Distribution.
"""Choose the Java tree the call-scan skill maps.

The skill scans whatever directory it is given. This module is the
repository-specific part. Fineract is the adapter shipped today. Another
repository is another function here.
"""

from pathlib import Path

FINERACT_LOANS = Path(
    "fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount"
)


def source_tree(repo: Path) -> tuple[Path, str] | None:
    """Return the tree to scan and its scope name, or None.

    A directory that is already the loans package is that tree. A Fineract
    checkout is mapped at its loans package. Scope stays ``loans`` for both.
    """
    if repo.name == "loanaccount" and repo.is_dir():
        return repo, "loans"
    candidate = repo / FINERACT_LOANS
    if candidate.is_dir():
        return candidate, "loans"
    return None
