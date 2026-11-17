"""Unit tests (rung 1) for the ribeiro-mangrove extract.py (a reference list with no measurements).

Hand-built fixture: 3 PDF pages of text lines with explicit vertical positions (27.7 pt inside an entry, 35.6 pt between
entries, as in the real file), including a page number line, an entry cut across a page break and a title line.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from coastal_crawler.compilations import ContractError, build_compilations, read_extracted, write_extracted
from coastal_crawler.compilations import vocab

VocabError = vocab.VocabError

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "ribeiro-mangrove" / "extract.py"
_spec = importlib.util.spec_from_file_location("ribeiro_extract", _EXTRACT)
assert _spec and _spec.loader
rb = importlib.util.module_from_spec(_spec)
sys.modules["ribeiro_extract"] = rb
_spec.loader.exec_module(rb)

SRC = rb.SOURCE_KEY


class _Page:
    def __init__(self, texts, gaps_before):
        top, self.lines = 74.0, []
        for t, g in zip(texts, gaps_before):
            top += g
            self.lines.append({"text": t, "x0": 69.0, "top": top})

    def extract_text_lines(self):
        return self.lines


class _Pdf:
    def __init__(self, pages):
        self.pages = pages


LINE, PAR = 27.7, 35.6
PAGE1 = _Page(
    ["List of studies used to build the litterfall model for mangroves", "Adame, M. F., A. Zaldivar, and J. Herrera.",
     "2013. Drivers of litterfall. Biotropica 45:147-154.", "Almeida, R. 2005. Ecologia de manguezais. Dissertation.",
     "Universidade de Sao Paulo, Brasil.", "Bush, P. G., and K. Smith. 1998. Grand Cayman. Pages 35-42 in B. Kjerfve,",
     "3"],
    [0, 35, LINE, PAR, LINE, PAR, PAR])
PAGE1.lines[-1]["x0"] = 540.0  # page number
PAGE2 = _Page(["editor. CARICOMP sites. UNESCO, Paris, France.", "Chale, F. M. M. 1996. Litter in Guyana. Hydrobiologia",
               "4"], [0, PAR, LINE])
PAGE2.lines[-1]["x0"] = 540.0
PAGE3 = _Page(["330:47-53.", "Day, J. W., and R. Day. 1987. Mangroves. Aquatic Botany 27:267-284."], [0, PAR])


def _use_pages(monkeypatch, pages):
    monkeypatch.setattr(rb, "LIST_PAGES", range(len(pages)))
    return _Pdf(pages)


def test_paragraphs_across_pages(monkeypatch):
    paras = rb.read_paragraphs(_use_pages(monkeypatch, [PAGE1, PAGE2, PAGE3]))
    assert paras == [
        "Adame, M. F., A. Zaldivar, and J. Herrera. 2013. Drivers of litterfall. Biotropica 45:147-154.",
        "Almeida, R. 2005. Ecologia de manguezais. Dissertation. Universidade de Sao Paulo, Brasil.",
        "Bush, P. G., and K. Smith. 1998. Grand Cayman. Pages 35-42 in B. Kjerfve, editor. CARICOMP sites. UNESCO, Paris, France.",
        "Chale, F. M. M. 1996. Litter in Guyana. Hydrobiologia 330:47-53.",  # page 3 line 1 continues the cut entry
        "Day, J. W., and R. Day. 1987. Mangroves. Aquatic Botany 27:267-284.",
    ]


def test_complete_entry_then_non_author_page_start_raises(monkeypatch):
    p1 = _Page(["List of studies used to build the litterfall model", "Chale, F. M. M. 1996. Litter. Hydrobiologia 330:47-53."], [0, 35])
    p2 = _Page(["not an author start"], [0])
    with pytest.raises(ContractError, match="previous paragraph is complete"):
        rb.read_paragraphs(_use_pages(monkeypatch, [p1, p2]))
    p1b = _Page(["Some other first line", "x"], [0, 35])
    with pytest.raises(ContractError, match="list title"):
        rb.read_paragraphs(_use_pages(monkeypatch, [p1b]))


def test_entries_by_inspection():
    paras = ["Adame, M. F., A. Zaldivar, and J. Herrera. 2013. Drivers of litterfall. Biotropica 45:147-154.",
             "Almeida, R. 2005. Ecologia de manguezais. Dissertation. Universidade de Sao Paulo, Brasil.",
             "Bush, P. G., and K. Smith. 1998. Grand Cayman. Pages 35-42 in B. Kjerfve, editor. UNESCO, Paris, France.",
             "Chale, F. M. M. 1996a. Litter in Guyana. Hydrobiologia 330:47-53.",
             "Pool, D. J., A. E. Lugo, and S. Snedaker. 1975. Litter. Proceedings of the International Symposium 1:213-217.",
             "Snedaker, S. C., and M. S. Brown. 1981. Water quality. United States Environmental Protection Agency.",
             "Longo, A. 2009. Produtividade. Thesis. Universidade Federal de Pernambuco."]
    ents = rb.parse_entries(paras)
    assert [e["key"] for e in ents] == [
        "ref:Adame et al. 2013", "ref:Almeida 2005", "ref:Bush and Smith 1998", "ref:Chale 1996a", "ref:Pool et al. 1975",
        "ref:Snedaker and Brown 1981", "ref:Longo 2009"]
    assert [e["publication_type"] for e in ents] == [
        "journal article", "dissertation", "book chapter", "journal article", "proceedings", None, "thesis"]
    assert [e["year"] for e in ents] == [2013, 2005, 1998, 1996, 1975, 1981, 2009]
    assert ents[0]["authors"] == "Adame, M. F., A. Zaldivar, and J. Herrera"
    assert ents[3]["year_tag"] == "1996a"


def test_parse_failures_raise():
    with pytest.raises(ContractError, match="does not match"):
        rb.parse_entries(["Adame, M. F. no year here at all."])
    ok = "Adame, M. F., and J. Herrera. 2013. T. J 1:1-2."
    with pytest.raises(ContractError, match="collide"):
        rb.parse_entries([ok, ok])


def test_factless_build_needs_the_flag(tmp_path):
    ents = rb.parse_entries(["Adame, M. F., and J. Herrera. 2013. T. J 1:1-2.", "Almeida, R. 2005. Ecologia. Dissertation. U."])
    tables, report = rb.build_tables(ents, "2026-11-15")
    assert report["facts"] == 0 and report["references"] == 2 and report["citations"] == 2
    assert list(tables["source"].license) == ["CC BY 3.0"]
    with pytest.raises(VocabError, match="no habitat label map declared"):  # the first of the two exemptions to be refused
        write_extracted(tables, tmp_path / "no")
    out = tmp_path / "ex"
    write_extracted(tables, out, allow_factless=frozenset({SRC}))
    back = read_extracted(out, frozenset({SRC}))
    assert sorted(back["reference"].reference_key) == ["ref:Adame and Herrera 2013", "ref:Almeida 2005"]
    assert sorted(back["citation"].year) == [2005, 2013]
    counts = build_compilations([out], tmp_path / "w.duckdb", frozenset({SRC}))
    assert counts["measurement_fact"] == 0 and counts["reference_dim"] == 2 and counts["location_dim"] == 0
