"""Tests for the OCR-page snippet matcher (site/snippets.py)."""

from __future__ import annotations

from coastal_crawler.site.snippets import find_snippet, split_pages

_DOC = (
    '<page number="0">\n\nIntro text with no numbers.\n\n</page>\n\n'
    '<page number="1">\n\nSalinity was measured at 28.4 PSU at the estuary mouth.\n\n</page>\n\n'
    '<page number="2">\n\nA table of nitrate values: 1,234.50 mg/L in the deep channel.\n\n</page>\n\n'
)


class TestSplitPages:
    def test_splits_on_page_tags(self) -> None:
        pages = split_pages(_DOC)
        assert [n for n, _ in pages] == [0, 1, 2]

    def test_no_tags_returns_empty(self) -> None:
        assert split_pages("plain text, no tags") == []


class TestFindSnippet:
    def test_literal_match(self) -> None:
        result = find_snippet(_DOC, "28.4", "salinity", "PSU")
        assert result.matched is True
        assert result.page_number == 1

    def test_numeric_normalized_match_strips_thousands_separator(self) -> None:
        result = find_snippet(_DOC, "1234.50", "nitrate", "mg/L")
        assert result.matched is True
        assert result.page_number == 2

    def test_no_value_falls_back_to_first_page(self) -> None:
        result = find_snippet(_DOC, None, "salinity", None)
        assert result.matched is False
        assert result.page_number == 0

    def test_unmatched_value_falls_back_to_attribute_mention(self) -> None:
        result = find_snippet(_DOC, "99.9", "salinity", None)
        assert result.matched is False
        assert result.page_number == 1  # only page mentioning "salinity"

    def test_no_pages_returns_none_page_number(self) -> None:
        result = find_snippet("no page tags here", "28.4", "salinity", None)
        assert result.page_number is None
        assert result.matched is False

    def test_prefers_page_matching_attribute_when_value_appears_on_multiple_pages(self) -> None:
        doc = (
            '<page number="0">\n\nsome other value 5.0 here\n\n</page>\n\n'
            '<page number="1">\n\nphosphate concentration was 5.0 umol/L\n\n</page>\n\n'
        )
        result = find_snippet(doc, "5.0", "phosphate", "umol/L")
        assert result.matched is True
        assert result.page_number == 1

    def test_literal_match_returns_unmarked_page_text(self) -> None:
        """`.text` is the raw OCR page text — no `<mark>` or other injected
        markup. judge_worker.py feeds this straight to the judge model as
        `context`; any injected marker would leak the answer location into
        what's supposed to be a blind judgement (see the 2026-08-20 fix that
        removed find_snippet()'s highlighting for exactly this reason)."""
        result = find_snippet(_DOC, "28.4", "salinity", "PSU")
        assert "<mark>" not in result.text
        assert "28.4" in result.text

    def test_does_not_match_digit_sequence_inside_a_larger_number(self) -> None:
        doc = '<page number="0">\n\nAn unrelated reading of 125.3 units was recorded.\n\n</page>\n\n'
        result = find_snippet(doc, "5", "attribute", None)
        assert result.matched is False

    def test_does_not_match_across_a_decimal_point(self) -> None:
        doc = '<page number="0">\n\nAn unrelated reading of 1234.50 units was recorded.\n\n</page>\n\n'
        result = find_snippet(doc, "50", "attribute", None)
        assert result.matched is False

    def test_does_not_match_positive_value_inside_a_negative_number(self) -> None:
        doc = '<page number="0">\n\nTemperature anomaly of -5.0 degrees was recorded.\n\n</page>\n\n'
        result = find_snippet(doc, "5.0", "attribute", None)
        assert result.matched is False

