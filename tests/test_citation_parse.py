"""Hand-built fixtures for citation_parse / citation_resolve / norm_doi.

Every expected value is stated by inspection of the input string. No network, no DB.
"""

from __future__ import annotations

from typing import Any

import pytest

from coastal_crawler.citation_parse import first_author_surname, parse_citation, tidy_title, trimmed_title_variants
from coastal_crawler.citation_resolve import resolve_title, title_match
from coastal_crawler.openalex_client import norm_doi


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.1016/J.ECSS.2013.05.010", "10.1016/j.ecss.2013.05.010"),
        ("https://doi.org/10.2307/1352438", "10.2307/1352438"),
        ("http://dx.doi.org/10.1016/j.sajb.2016.05.004", "10.1016/j.sajb.2016.05.004"),
        ("doi:10.1002/lno.12374", "10.1002/lno.12374"),
    ],
)
def test_norm_doi(raw: str, expected: str) -> None:
    assert norm_doi(raw) == expected


@pytest.mark.parametrize("raw", ["nan", "https://link.springer.com/article/10.1007/x", "Smith 2019"])
def test_norm_doi_rejects_non_doi(raw: str) -> None:
    with pytest.raises(ValueError):
        norm_doi(raw)


# (text, year, expected style, expected title, expected first-author surname)
CASES = [
    ("Gardner, W. S., Arbor, A., & Rowe, G. T. (1993). Ammonium Excretion by Benthic Invertebrates and "
     "Sediment-Water Nitrogen Flux in the Gulf of Mexico Near the Mississippi River Outflow. Estuaries, 16(4), 799–808.",
     1993, "apa", "Ammonium Excretion by Benthic Invertebrates and Sediment-Water Nitrogen Flux in the Gulf of Mexico "
     "Near the Mississippi River Outflow", "gardner"),
    # APA with a numbered subtitle and an initials-bearing title ("N.W.")
    ("Smith, K. L. (1978). Benthic community respiration in the N.W. Atlantic Ocean: in situ measurements from 40 to "
     "5200 m. Marine Biology, 47(4), 337–347.", 1978, "apa",
     "Benthic community respiration in the N.W. Atlantic Ocean: in situ measurements from 40 to 5200 m", "smith"),
    ("Pamatmat, M. M. (1971). Oxygen consumption by the seabed. IV. Shipboard and laboratory experiments. "
     "Limnology and Oceanography, 16(3), 536–550.", 1971, "apa",
     "Oxygen consumption by the seabed. IV. Shipboard and laboratory experiments", "pamatmat"),
    ("Trono, Gavino C., and Arturo O. Lluisma. 1990. “Seasonality of Standing Crop of a Sargassum Bed in Bolinao.” "
     "Hydrobiologia 204: 331–45.", 1990, "quoted", "Seasonality of Standing Crop of a Sargassum Bed in Bolinao", "trono"),
    ("Kauffman, J.B., Giovanonni, L., Kelly, J., 2020. Total ecosystem carbon stocks at the marine‐terrestrial "
     "interface: Blue carbon of the Pacific Northwest Coast, United States. Global Change Biology, 26(10).",
     2020, "author_year", "Total ecosystem carbon stocks at the marine‐terrestrial interface: Blue carbon of the "
     "Pacific Northwest Coast, United States", "kauffman"),
    ("Kristensen, E. et al. Emission of CO2 and CH4 to the atmosphere by sediments and open waters in two Tanzanian "
     "mangrove forests. Mar. Ecol. Prog. Ser. 370, 53–67 (2008)", 2008, "sentence",
     "Emission of CO2 and CH4 to the atmosphere by sediments and open waters in two Tanzanian mangrove forests",
     "kristensen"),
    ("Kitidis, V., Upstill-Goddard, R. C. & Anderson, L. G. Methane and nitrous oxide in surface water along the "
     "North-West Passage, Arctic Ocean. Mar. Chem. 121, 80–86 (2010).", 2010, "sentence",
     "Methane and nitrous oxide in surface water along the North-West Passage, Arctic Ocean", "kitidis"),
    ("Cheung, H.L.S., et al., Denitrification, anammox, and DNRA in oligotrophic continental shelf sediments. "
     "Limnology and Oceanography, 2024. 69: 621-637.", 2024, "embedded",
     "Denitrification, anammox, and DNRA in oligotrophic continental shelf sediments", "cheung"),
]


@pytest.mark.parametrize("text,year,style,title,author", CASES)
def test_parse_free_text(text: str, year: int, style: str, title: str, author: str) -> None:
    p = parse_citation(text, year)
    assert (p.style, p.title, p.first_author, p.year) == (style, title, author, year)


def test_fields_style_trusts_title_and_strips_artifact() -> None:
    p = parse_citation("ignored", 2021, title_field="C.: Transcriptional responses of Trichodesmium to Fe and P",
                       authors_field="Cerdan-Garcia, E.; Smith, J.")
    assert p.style == "fields" and p.title == "Transcriptional responses of Trichodesmium to Fe and P"
    assert p.first_author == "cerdan-garcia"


def test_author_year_only_and_unparsed_are_explicit() -> None:
    assert parse_citation("Lantz et al., 2014. Coral Reefs", 2014).style == "author_year_only"
    assert parse_citation("Lantz et al., 2014. Coral Reefs", 2014).first_author == "lantz"
    assert parse_citation("data come from Figure 2.", None).style == "unparsed"


@pytest.mark.parametrize(
    "text,surname",
    [("Tania L. Maxwell and André S. Rovai", "maxwell"), ("S. Al-Rousan, B. Smith", "al-rousan"),
     ("Shaopan Xia and X. Y.", "xia"), ("Van Rooij, J. M., Videler", "rooij"), ("B.D. Eyre, and L. Davison", "eyre")],
)
def test_first_author_surname(text: str, surname: str) -> None:
    assert first_author_surname(text) == surname


def test_tidy_title() -> None:
    assert tidy_title("Dataset: Soil carbon in Oregon marshes") == "Soil carbon in Oregon marshes"


def test_title_match_tolerates_typography_but_not_different_titles() -> None:
    assert title_match("Sediment–Water Exchange of Nutrients", "Sediment-Water Exchange of Nutrients")
    assert not title_match("Low rates of dinitrogen fixation", "Evidence of active nitrogen uptake in the sea")


# ---- resolver, with a fake client returning canned OpenAlex results -------------------------------

def _work(wid: str, title: str, year: int, authors: list[str], doi: str | None) -> dict[str, Any]:
    return {"id": f"https://openalex.org/{wid}", "title": title, "publication_year": year, "type": "article",
            "doi": f"https://doi.org/{doi}" if doi else None,
            "authorships": [{"author": {"display_name": a}} for a in authors]}


class FakeClient:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"results": self.results}


TITLE = "Capturing of organic carbon and nitrogen in eelgrass sediments of southern Scandinavia"


def _p(year: int = 2023, author: str = "leiva-duenas"):
    return parse_citation("x", year, title_field=TITLE, authors_field=f"{author}, C.")


def test_resolver_resolved_strong() -> None:
    w = _work("W1", TITLE, 2023, ["Carmen Leiva-Dueñas", "Anna Graversen"], "10.1002/lno.12374")
    r = resolve_title(FakeClient([w]), _p(), per_page=5)  # type: ignore[arg-type]
    assert (r.status, r.openalex_id, r.doi) == ("resolved", "W1", "10.1002/lno.12374")


def test_resolver_year_contradiction_with_distinctive_title_is_weak() -> None:
    w = _work("W1", TITLE, 2019, ["Carmen Leiva-Dueñas"], "10.1002/lno.12374")
    r = resolve_title(FakeClient([w]), _p(), per_page=5)  # type: ignore[arg-type]
    assert r.status == "resolved_weak" and r.evidence["year"] is False


def test_resolver_rejects_when_author_and_year_both_contradict() -> None:
    w = _work("W1", TITLE, 2001, ["Someone Else"], "10.1/x")
    assert resolve_title(FakeClient([w]), _p(), per_page=5).status == "no_match"  # type: ignore[arg-type]


def test_resolver_prefers_article_over_dataset_duplicate() -> None:
    art = _work("W1", TITLE, 2023, ["Carmen Leiva-Dueñas"], "10.1002/lno.12374")
    ds = _work("W2", "Supplementary original data for '" + TITLE + "'", 2023, ["Carmen Leiva-Dueñas"], "10.6084/x")
    r = resolve_title(FakeClient([ds, art]), _p(), per_page=5)  # type: ignore[arg-type]
    assert (r.status, r.openalex_id) == ("resolved", "W1")


def test_resolver_ambiguous_on_two_equally_good_distinct_works() -> None:
    a = _work("W1", TITLE, 2023, ["Carmen Leiva-Dueñas"], "10.1/a")
    b = _work("W2", TITLE, 2023, ["Carmen Leiva-Dueñas"], "10.1/b")
    assert resolve_title(FakeClient([a, b]), _p(), per_page=5).status == "ambiguous"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "full,trimmed",
    [
        ("Primary production in Dumbell Bay in the Arctic Ocean, Marine Biology, 61, 41-51, 1980.",
         "Primary production in Dumbell Bay in the Arctic Ocean"),
        ("Impacts of Nutrient Reduction on Coastal Communities, Ecosystems, 10, 96-119, 10.1007/s10021-006-9000, 2007.",
         "Impacts of Nutrient Reduction on Coastal Communities"),
        ("Litter production and turnover in basin mangrove forests in Southwest Florida. Ecology 67:670–683.",
         "Litter production and turnover in basin mangrove forests in Southwest Florida"),
    ],
)
def test_trimmed_title_variants(full: str, trimmed: str) -> None:
    assert trimmed in trimmed_title_variants(full)


def test_trimmed_title_variants_leaves_clean_title_alone() -> None:
    assert trimmed_title_variants("Capturing of organic carbon and nitrogen in eelgrass sediments") == []


def test_resolver_dataset_record_never_strong() -> None:
    ds = _work("W9", TITLE, 2023, ["Carmen Leiva-Dueñas"], "10.1594/pangaea.1")
    ds["type"] = "dataset"
    r = resolve_title(FakeClient([ds]), _p(), per_page=5)  # type: ignore[arg-type]
    assert r.status == "resolved_weak" and r.evidence["dataset"] is True


# ---- bibliography matching ------------------------------------------------------------------------
from coastal_crawler.bibliography import BibEntry, match_label, parse_label  # noqa: E402


def _e(text: str, first: str, year: int) -> BibEntry:
    return BibEntry("f", 0, text, first, year, None, None)


def test_parse_label_variants() -> None:
    a = parse_label("Atkinson & Grigg, 1984. Coral Reefs")
    assert (a.first, a.second, a.year, a.hint) == ("atkinson", "grigg", 1984, "Coral Reefs")
    b = parse_label("Beman et al., 2021. Nature Comms")
    assert (b.first, b.second, b.year) == ("beman", None, 2021)
    assert parse_label("data come from Figure 2.") is None


def test_match_label_unique_second_author_and_hint() -> None:
    es = [_e("Atkinson, M. J. and Grigg, R. W.: Model of a coral reef ecosystem, Coral Reefs, 3, 13, 1984.", "atkinson", 1984),
          _e("Atkinson, M. J. and Smith, S.: C:N:P ratios, Limnol. Oceanogr., 28, 568, 1984.", "atkinson", 1984)]
    s, c = match_label("Atkinson & Grigg, 1984. Coral Reefs", es)
    assert s == "matched" and c[0] is es[0]
    s, c = match_label("Atkinson et al. 1984", es)
    assert s == "ambiguous" and len(c) == 2
    assert match_label("Atkinson et al. 1990", es)[0] == "no_entry"


def test_match_label_hint_breaks_tie_and_secondary_is_skipped() -> None:
    es = [_e("Beman, J. M.: x, Nature Communications, 12, 2021.", "beman", 2021),
          _e("Beman, J. M.: y, Limnology and Oceanography, 66, 2021.", "beman", 2021)]
    s, c = match_label("Beman et al., 2021. Nature Comms", es)
    assert s == "matched" and "Nature" in c[0].text
    assert match_label("Anadon 1980 in Lamela-Silvarrey et al. 2012", es)[0] == "secondary_citation"


def test_match_label_year_suffix_is_never_matched() -> None:
    es = [_e("Pessarrodona, A.; Smale, D. A.: Can ecosystem functioning be maintained, J. Ecol., 2018.", "pessarrodona", 2018)]
    assert match_label("Pessarrodona et al. 2018b", es)[0] == "year_suffix"
    assert match_label("Pessarrodona et al. 2018", es)[0] == "matched"


def test_load_numbered_layout_drops_footers_and_stops_at_marker() -> None:
    from coastal_crawler.bibliography import load_numbered_layout
    raw = """References
1.    Hughes, T. P. et al. Coral reefs in the anthropocene. Nature 546, 82–90 (2017).
2.    Shaw, E. C., Mcneil, B. I. & Bates, M. L. Anthropogenic changes to seawater
      buffer capacity. Glob. Change Biol. 19, 1632–1641 (2013).
COMMUNICATIONS EARTH & ENVIRONMENT | https://doi.org/10.1038/x
3.    Bates, N. R. Twenty years of marine carbon cycle observations. Front. Mar. Sci. 4, 36 (2017).
Acknowledgements
We thank everyone."""
    es = load_numbered_layout(raw, "t", "Acknowledgements", r"COMMUNICATIONS|^ARTICLE")
    assert [e.first_author for e in es] == ["hughes", "shaw", "bates"]
    assert [e.year for e in es] == [2017, 2013, 2017]
    assert "COMMUNICATIONS" not in es[1].text and "thank" not in es[2].text


# ---- source kind ----------------------------------------------------------------------------------
from coastal_crawler.source_kind import (  # noqa: E402
    kind_from_doi_prefix, kind_from_openalex_type, kind_from_publication_type, kind_from_text)


@pytest.mark.parametrize(
    "text,kind",
    [
        ("calcofi.org", "website_or_database"),
        ("hahana.soest.hawaii.edu", "website_or_database"),
        ("MAREANO geochemistry database_Accessed Sept-2021", "website_or_database"),
        ("Soetaert, K. et al. Trait-based products of EMODnet benthic biology. (2018). http://www.emodnet-biology.eu", "website_or_database"),
        ("Conte, 2015, pers. comm.", "personal_or_unpublished"),
        ("Glud, R.N. et al. unpubl.", "personal_or_unpublished"),
        ("Twilley, R.R., 1982, Litter dynamics. Ph.D. Dissertation, Louisiana State University.", "thesis"),
        ("Steven, D. M. Gulf of St. Lawrence Project: Final Report, McGill University, 113p., 1974.", "report"),
        ("Kristensen, E. et al. Emission of CO2 and CH4. Mar. Ecol. Prog. Ser. 370, 53–67 (2008)", "paper"),
        ("Lantz et al., 2014. Coral Reefs", "unknown"),  # author-year label only: not enough to say
        ("Sci. Rep. 8, 6079 (2018)", "paper"),  # 'Rep.' in a journal name is not a report
    ],
)
def test_kind_from_text(text: str, kind: str) -> None:
    assert kind_from_text(text)[0] == kind


def test_paper_with_doi_link_is_not_a_website() -> None:
    t = "Leiva-Dueñas, C. et al. Capturing of organic carbon. Limnol. Oceanogr. 68, 631-648 (2023). https://doi.org/10.1002/lno.12374"
    assert kind_from_text(t)[0] == "paper"


def test_kind_from_other_evidence() -> None:
    assert kind_from_openalex_type("dataset") == ("dataset", "openalex_type:dataset")
    assert kind_from_openalex_type("data-paper")[0] == "paper"  # a paper describing a dataset
    assert kind_from_openalex_type("standard")[0] == "unknown"
    assert kind_from_publication_type("primary dataset")[0] == "dataset"
    assert kind_from_publication_type("associated source") is None  # source label alone does not say
    assert kind_from_doi_prefix("10.1594/pangaea.716104")[0] == "dataset"
    assert kind_from_doi_prefix("10.5281/zenodo.1") is None  # Zenodo hosts papers too


def test_bibliographic_links_and_emails() -> None:
    cerco = "Cerco, C. F. Sediment-water column exchanges of nutrients and oxygen in the tidal James and Appomattox rivers. VIMS Special Report 1985. http://scholar.google.com/scholar?hl=en&q=cerco"
    assert kind_from_text(cerco)[0] == "report"  # not a website: Google Scholar is an index
    assert kind_from_text("Norbert Wasmund, Leibniz Institute for Baltic Sea Research, norbert.wasmund@io-warnemuende.de")[0] == "personal_or_unpublished"
    assert kind_from_text("Thorne, K. 2015. Data release. https://www.sciencebase.gov/catalog/item/5a0b")[0] == "website_or_database"
