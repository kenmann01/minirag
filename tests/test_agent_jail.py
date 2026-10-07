"""Repository tools stay inside the repo root."""

from app.agent import list_directory, read_text, resolve_inside


def test_reads_stay_inside_the_repo_and_listings_are_sorted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "b.txt").write_text("bee", encoding="utf-8")
    (repo / "a.txt").write_text("aye", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")

    assert list_directory(repo, ".") == "a.txt\nb.txt"
    assert read_text(repo, "a.txt") == "aye"
    try:
        resolve_inside(repo, "../secret.txt")
    except PermissionError:
        return
    raise AssertionError("path escape was allowed")
