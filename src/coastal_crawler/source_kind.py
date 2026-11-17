"""What KIND of thing is a citation: a paper you could download, a dataset, a website/database,
a personal communication, ...? Evidence-ranked and explicit; returns 'unknown' instead of guessing.

Evidence, strongest first (each classifier returns (kind, basis) so the basis is auditable):
  1. OpenAlex work type                         (kind_from_openalex_type)
  2. the compilation's own publication_type     (kind_from_publication_type)
  3. DOI registrant prefix of a data repository (kind_from_doi_prefix)
  4. narrow wording in the label text           (kind_from_text)

Kinds: paper | book_or_chapter | thesis | report | dataset | website_or_database |
       personal_or_unpublished | unknown
``is_document`` kinds (paper, book_or_chapter, thesis, report) are things with text you could
download and read; dataset / website_or_database / personal_or_unpublished are not.
"""

from __future__ import annotations

import re

DOCUMENT_KINDS = frozenset({"paper", "book_or_chapter", "thesis", "report"})

_OA_TYPE = {
    "article": "paper", "review": "paper", "preprint": "paper", "letter": "paper", "editorial": "paper",
    "conference-paper": "paper", "data-paper": "paper", "book": "book_or_chapter", "book-chapter": "book_or_chapter",
    "dissertation": "thesis", "report": "report", "dataset": "dataset",
}

_PUBLICATION_TYPE = {
    "journal article": "paper", "article": "paper", "proceedings": "paper", "book chapter": "book_or_chapter",
    "thesis": "thesis", "dissertation": "thesis", "primary dataset": "dataset", "synthesis dataset": "dataset",
    "associated data": "dataset",
}  # 'synthesis source' / 'associated source' / 'primary source' / 'combined dataset and manuscript' stay unknown

# DOI registrants that mint DOIs for DATA records. Zenodo (10.5281) and figshare (10.6084) also host
# papers, so they are NOT listed: for those only the OpenAlex type can say.
DATA_DOI_PREFIXES = {
    "10.1594": "PANGAEA", "10.5065": "NCAR/UCAR", "10.25573": "Smithsonian SERC", "10.7910": "Harvard Dataverse",
    "10.5066": "USGS data", "10.15468": "GBIF", "10.1575": "WHOI/BCO-DMO", "10.6073": "EDI (PASTA)",
    "10.5285": "CEDA", "10.14466": "Cefas", "10.17528": "CIFOR data", "10.5061": "Dryad",
}

_PERSONAL = re.compile(r"pers\.?\s*comm|personal comm|\bunpubl|\bin prep\b|\bsubmitted\b|[\w.+-]+@[\w-]+\.[\w.]+", re.I)
# Links to publishers / indexes / resolvers are bibliographic, not data websites: ignored by the URL cue.
_BIBLIOGRAPHIC_URL = re.compile(
    r"https?://(?:[\w-]+\.)*(?:scholar\.google\.[a-z.]+|doi\.org|handle\.net|jstor\.org|sciencedirect\.com|springer\.com|"
    r"wiley\.com|researchgate\.net|semanticscholar\.org|tandfonline\.com|oup\.com|cambridge\.org|nature\.com|"
    r"pubs\.acs\.org|ingentaconnect\.com|int-res\.com|plos\.org|frontiersin\.org|copernicus\.org|mdpi\.com)\S*", re.I)
_B, _E = r"(?<![A-Za-z])", r"(?![A-Za-z])"  # word edges that treat '_' as a separator ('database_Accessed')
_DB_WORDS = re.compile(
    _B + r"(?:data\s?base|data\s?sets?|data (?:archive|portal|repository|product|center|centre)s?|accessed|"
    r"BCO-?DMO|NCEI|PANGAEA|figshare|zenodo|GBIF)" + _E, re.I)
_URL = re.compile(r"https?://(?!(?:dx\.)?doi\.org)\S+|\bwww\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|edu|gov|net|eu|de|fr|jp)\b/?", re.I)
_THESIS = re.compile(r"\bph\.?\s?d\b|\bthesis\b|\bdissertation\b|\bdiploma\b|\bmaster'?s\b", re.I)
_REPORT = re.compile(r"\b(?:final |technical |annual |data )?report\b|\bdata rep\b|\bmemorandum\b", re.I)
_PAPER_CUE = re.compile(  # volume+page range | volume(issue):page | volume, article-number (year)
    r"\b\d{1,4}\s*[,:(]\s*\d{1,5}\s*[-–]\s*\d{1,5}\b|\b\d{1,4}\s*\(\d{1,3}\)\s*[,:]\s*\d{1,5}|"
    r"\b\d{1,4},\s*\d{3,6}\s*\((?:19|20)\d\d\)", re.I)


def kind_from_openalex_type(oa_type: str) -> tuple[str, str]:
    return _OA_TYPE.get(oa_type, "unknown"), f"openalex_type:{oa_type}"


def kind_from_publication_type(pub_type: str | None) -> tuple[str, str] | None:
    k = _PUBLICATION_TYPE.get((pub_type or "").lower())
    return (k, f"source_publication_type:{pub_type}") if k else None


def kind_from_doi_prefix(doi: str) -> tuple[str, str] | None:
    prefix = doi.split("/", 1)[0].lower()
    return ("dataset", f"data_repository_prefix:{DATA_DOI_PREFIXES[prefix]}") if prefix in DATA_DOI_PREFIXES else None


def kind_from_text(text: str | None) -> tuple[str, str]:
    """Narrow, auditable wording rules on the label. 'unknown' unless a cue is explicit."""
    if not text:
        return "unknown", "no_text"
    t = re.sub(r"\bdoi:\s*\S+", "", _BIBLIOGRAPHIC_URL.sub("", text), flags=re.I)  # DOI/publisher links are not websites
    if _PERSONAL.search(t):
        return "personal_or_unpublished", f"text:{_PERSONAL.search(t).group(0).lower()}"
    paper_cue = _PAPER_CUE.search(t)
    db = _DB_WORDS.search(t) or (_URL.search(t) if not paper_cue else None)
    if db:
        return "website_or_database", f"text:{db.group(0).lower()[:40]}"
    if _THESIS.search(t):
        return "thesis", f"text:{_THESIS.search(t).group(0).lower()}"
    if _REPORT.search(t) and not paper_cue:
        return "report", f"text:{_REPORT.search(t).group(0).lower()}"
    if paper_cue:
        return "paper", "text:volume_pages_pattern"
    return "unknown", "no_cue"
