"""An empty corpus must not wipe the stored index."""

from app.corpus.ingest import EmptyCorpusError, run


class BoomAdapter:
    def connect(self):
        raise AssertionError("empty corpus must not open the database")


def test_empty_corpus_does_not_open_the_database(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "blank.md").write_text("no numbered heading here\n", encoding="utf-8")
    try:
        run(BoomAdapter(), corpus_dir=corpus)
    except EmptyCorpusError as exc:
        assert "no chunks" in str(exc)
    else:
        raise AssertionError("expected EmptyCorpusError")
