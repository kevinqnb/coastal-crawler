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

    def test_literal_match_highlights_value(self) -> None:
        result = find_snippet(_DOC, "28.4", "salinity", "PSU")
        assert "<mark>28.4</mark>" in result.text

    def test_numeric_normalized_match_highlights_original_formatting(self) -> None:
        result = find_snippet(_DOC, "1234.50", "nitrate", "mg/L")
        assert "<mark>1,234.50</mark>" in result.text

    def test_unmatched_value_is_not_highlighted(self) -> None:
        result = find_snippet(_DOC, "99.9", "salinity", None)
        assert "<mark>" not in result.text

    def test_highlights_all_occurrences_of_a_repeated_literal_value(self) -> None:
        doc = '<page number="0">\n\n28.4 PSU, then again 28.4 PSU later.\n\n</page>\n\n'
        result = find_snippet(doc, "28.4", "salinity", "PSU")
        assert result.text.count("<mark>28.4</mark>") == 2

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

    def test_highlight_does_not_mark_substring_of_a_larger_number(self) -> None:
        doc = '<page number="0">\n\nA reading of 5.0 units, unrelated 125.0 elsewhere.\n\n</page>\n\n'
        result = find_snippet(doc, "5.0", "attribute", None)
        assert result.text.count("<mark>5.0</mark>") == 1
        assert "<mark>125.0</mark>" not in result.text
        assert "1<mark>25.0</mark>" not in result.text
