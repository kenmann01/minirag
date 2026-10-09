"""PDF conversion fails loud when a document has no numbered sections."""

from pathlib import Path

import fitz

from app.corpus.convert import convert_dir, to_markdown
from scripts.convert_docs import main


def _pdf(path: Path, text: str) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(72, 72, 540, 720), text, fontsize=11)
    document.save(path)
    document.close()


def test_numbered_headings_become_markdown_sections():
    markdown = to_markdown(
        "Effective Date: 2026-04-01\n\n"
        "12. Provisioning limits\n"
        "A lender shall keep a limit.\n\n"
        "Section 13. Disclosure\n"
        "The lender states the rate.\n"
    )
    assert markdown is not None
    assert "**Effective Date:** 2026-04-01" in markdown
    assert "## 12. Provisioning limits" in markdown
    assert "## 13. Disclosure" in markdown
    assert "A lender shall keep a limit." in markdown


def test_cfr_section_symbol_uses_the_section_number():
    markdown = to_markdown("§ 1026.18 Content of disclosures\nThe creditor shall disclose.\n")
    assert markdown is not None
    assert "## 18. Content of disclosures" in markdown


def test_a_numbered_sentence_is_not_a_heading():
    markdown = to_markdown(
        "1. Purpose\nA lender shall disclose.\n"
        "2. Accept no unnecessary risk. Flying is not possible without it.\n"
    )
    assert markdown is not None
    assert "## 1. Purpose" in markdown
    assert "## 2." not in markdown


def test_a_bare_section_marker_takes_the_next_title_once():
    markdown = to_markdown(
        "2-1\nIntroduction\nA pilot decides.\n2-1\nThe same header repeats.\n2-2\nRisk\nJudgment can be taught.\n"
    )
    assert markdown is not None
    assert markdown.count("## 1. Introduction") == 1
    assert "## 2. Risk" in markdown
    assert "The same header repeats." in markdown


def test_zero_sections_are_not_written(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "md"
    pdf_dir.mkdir()
    _pdf(pdf_dir / "empty.pdf", "This page has no numbered heading at all.")
    assert convert_dir(pdf_dir, out_dir) == 1
    assert list(out_dir.glob("*.md")) == []
    assert main([str(pdf_dir), "--out", str(out_dir)]) == 1


def test_a_good_pdf_is_kept_when_a_sibling_fails(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "md"
    pdf_dir.mkdir()
    _pdf(pdf_dir / "rules.pdf", "1. Purpose\nA lender shall disclose the rate.\n")
    _pdf(pdf_dir / "notes.pdf", "Just a cover page.\n")
    assert convert_dir(pdf_dir, out_dir) == 1
    written = list(out_dir.glob("*.md"))
    assert [path.name for path in written] == ["rules.md"]
    assert "## 1. Purpose" in written[0].read_text(encoding="utf-8")


def test_title_text_rejects_lines_that_cannot_be_titles():
    from app.corpus.convert import _title_text

    assert _title_text("") is None
    assert _title_text("word " * 30) is None
    assert _title_text("Ends with a period.") is None
    assert _title_text("lowercase start") is None
    assert _title_text("Figure 12 the chart") is None


def test_a_bare_cfr_marker_waits_for_its_title():
    from app.corpus.convert import _marker

    assert _marker("§ 1026.5") == "5"


def test_title_on_next_returns_none_at_the_end_of_the_document():
    from app.corpus.convert import _title_on_next

    assert _title_on_next(["§ 1026.5", "", ""], 0) is None


def test_a_marker_takes_a_title_separated_by_a_blank_line():
    markdown = to_markdown("§ 1026.4\n\nLate Payments\n\nA late payment is late.\n")
    assert markdown is not None
    assert "## 4. Late Payments" in markdown
    assert "A late payment is late." in markdown


def test_a_marker_without_a_valid_title_keeps_a_generic_one():
    markdown = to_markdown(
        "§ 1026.7\n\nthis cannot be a Title because it starts lowercased\n\nBody.\n"
    )
    assert markdown is not None
    assert "## 7. Section 7" in markdown
    assert "Body." in markdown


def test_an_empty_pdf_folder_reports_failure(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    assert convert_dir(pdf_dir, tmp_path / "md") == 1


def test_a_stale_markdown_file_is_removed_when_the_pdf_now_fails(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "md"
    pdf_dir.mkdir()
    out_dir.mkdir()
    _pdf(pdf_dir / "broken.pdf", "no numbered heading at all")
    (out_dir / "broken.md").write_text("stale output", encoding="utf-8")
    assert convert_dir(pdf_dir, out_dir) == 1
    assert not (out_dir / "broken.md").exists()
