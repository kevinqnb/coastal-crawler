# Task prompt — make scholarlm robust to unknown chandra-ocr-2 labels

Hand-off prompt for a **separate Claude Code session run inside the `scholarlm`
checkout** (`/projectnb/mcnet/kevin/coastal/scholarlm`). Written to start cold —
it assumes no knowledge of the coastal-crawler session that produced it.

Context for *this* repo: 40 papers are stuck in `ocr_failed` (20 on
`Chemical-Block`, 20 on `Bibliography`), all from `chandra_format.py`'s hard
`ValueError` on any `data-label` outside its 12-label audited set. Once the
scholarlm session below lands and reports back its API shape, we bump the
`scholarlm` pin in `pyproject.toml` + `uv.lock`, add
`Settings.doc_lm_unknown_label_policy` + `metrics.json` surfacing here, then walk
the re-OCR staging ladder for the 40.

---

## Make scholarlm's chandra-ocr-2 formatter robust to unknown layout labels

### Repo & branch

Work in this scholarlm checkout on branch `advances` (currently ~2 commits
ahead of `f928cae`, which is what my downstream project pins). Do NOT work on
`naacl`. Start from the tip of `advances`:

    git checkout advances && git pull --ff-only   # or fetch as appropriate

Read scholarlm's own CLAUDE.md / contributing conventions / build-note format
first and follow them. The relevant prior build notes are:
  notes/scholarlm/builds/2026-08-13-chandra-ocr-adapter-01.md
  notes/scholarlm/builds/2026-08-20-per-document-isolation-01.md

### The problem

`scholarlm/utils/chandra_format.py`'s `_format_page()` validates every
`<div data-label="...">` region chandra-ocr-2 emits against a hard-coded
allow-list (`_DROP_LABELS`, `_FIGURE_LABELS`, `_PLAIN_LABELS`, `_TABLE_LABEL`,
`_CAPTION_LABEL`) and raises `ValueError` on anything else:

    Unrecognized chandra-ocr-2 data-label 'Chemical-Block'; not in the audited
    label set from notes/scholarlm/builds/2026-08-13-chandra-ocr-adapter-01.md.

That allow-list was audited from only 20 PDFs. On a larger corpus, chandra
emits many more labels (`Chemical-Block`, `Bibliography`, and likely `Title`,
`Formula`, `Code`, `Handwriting`, `Form`, ...). A downstream project just lost
40 documents to exactly this: 20 on `Chemical-Block`, 20 on `Bibliography`.
`DocumentLM.fit()` catches the ValueError per-document (documentlm.py ~line
287), so the whole document's OCR text becomes `None` — one unknown region
kills the entire paper.

Per-label patching is a treadmill. We want a structural generalization.

### Required change 1 — structure-based fallback for unrecognized labels

In `_format_page()`, replace the terminal `raise ValueError` with a fallback
that classifies by the div's *contents*, not its label name (label names
churn; the HTML shape inside is stable):

  - div contains a top-level `<table>`  -> handle as table (reuse the
    existing `_TABLE_LABEL` branch logic / numbering)
  - else div contains a non-decorative `<img>` (after the existing
    decorative-image stripping at the top of `_format_page`) -> handle as
    figure (reuse the existing `_FIGURE_LABELS` branch logic / numbering)
  - else -> unwrap and emit its text children as body text (identical to the
    `_PLAIN_LABELS` branch: flush any pending_caption, append a
    `{"kind": "text", ...}` entry, reset open_entry)

`_DROP_LABELS` must stay a curated, explicit set — never a fallback
destination. Dropping content is the dangerous direction and stays opt-in
per label.

### Required change 2 — this fallback must be observable, not silent

A fallback nobody can see is a silent fallback. Track every coercion and
surface it parallel to the existing `format_errors` channel:

  - `DocumentLM` gains `self.coerced_labels: dict[int, dict[str, int]]`
    (doc_idx -> {label: count}), reset per `fit()` call just like
    `format_errors` / `context_length_exceeded_docs`.
  - `format_chandra_output()` / `_format_page()` need to report which labels
    they coerced and how (to table / figure / text). Pick a clean mechanism —
    return a tuple, or take a mutable collector arg — and thread it through.
  - Keep `format_errors` behavior unchanged for the genuine-error path.

### Required change 3 — gate it behind a policy flag (default = current behavior)

Add `unknown_label_policy: str = "raise"` to `DocumentLM.__init__`
(alongside `fast`, `drop_references`, etc.), thread it into
`format_chandra_output`. Accept `"raise"` (today's behavior — unknown label
still raises ValueError) and `"coerce"` (apply change 1 + record per change
2). Default stays `"raise"` so existing scholarlm users are unaffected.
Validate the value and raise on anything else.

### Required change 4 — fix `drop_references_section` for div-encoded headings

`scholarlm/utils/references.py`'s `_TAG_HEADING_RE` only matches a references
heading when the heading *text* sits between a tag-close and tag-open
(`>References<`). chandra sometimes encodes the reference list as a single
`<div data-label="Bibliography">...</div>` with no matching heading text
node, so truncation misses it and the div reaches label validation.

Extend `drop_references_section` to also cut the document at:
  - a `<div>` whose `data-label` is `Bibliography` (or `References`), and/or
  - a `<div data-label="Section-Header">` whose text content matches the
    existing `_HEADING_WORDS` pattern

Keep the existing olmOCR bare-line path and the open-`<page>` repair logic.
Everything from the cut point to end-of-document is dropped, as today.

Note: do NOT add `Bibliography` to `chandra_format.py`'s `_DROP_LABELS`.
Rationale: if chandra ever mislabels a real body paragraph as `Bibliography`,
`_DROP_LABELS` silently deletes it (a quietly-wrong result), whereas the
change-4 fix only triggers on an actual references heading and the change-1
fallback otherwise coerces it to text (noisy but complete).

### `Chemical-Block` specifically

I don't have a real sample of a `Chemical-Block` region. Under change 1 it
will coerce to text (formulae as text are useful downstream) unless it wraps
an `<img>`, in which case -> figure. That's an acceptable default. If you can
find a real chandra `Chemical-Block` example (test fixtures, cached output,
chandra-ocr-2 model card / docs) and it warrants explicit handling, add it to
`_PLAIN_LABELS` (or `_FIGURE_LABELS`) with a comment and a fixture — but
don't block on it.

### Tests (follow scholarlm's existing test layout/conventions)

Add unit tests for `format_chandra_output` / `_format_page` with
`unknown_label_policy="coerce"`:
  - unknown-label div containing a `<table>`  -> rendered as `<table number=..>`
  - unknown-label div containing a non-decorative `<img>` -> rendered as figure
  - unknown-label div with only text -> unwrapped as body text
  - unknown-label div that's empty after decorative-image stripping -> no output,
    no crash
  - `unknown_label_policy="raise"` (default) -> still raises ValueError on an
    unknown label (regression guard)
  - coerced labels are recorded in the reporting channel with correct counts
Add tests for `drop_references_section`:
  - `<div data-label="Bibliography">` truncates the doc
  - `<div data-label="Section-Header">References</div>` truncates the doc
  - existing heading-text and olmOCR bare-line cases still pass unchanged

Critical regression check: any document that does NOT contain an unknown
label must produce byte-identical formatter output before and after this
change (the coerce path must not touch the shared table/figure counters or
any recognized-label branch for those docs). Add a fixture that exercises
several recognized labels + a table + a figure and assert the output string
is unchanged from a checked-in golden.

### Out of scope

- The olmOCR (non-chandra) format branch — leave it alone.
- Any model-calling / PDF-rendering code.
- Any downstream/consumer repo — this task is scholarlm only.
- Bumping version pins anywhere.

### Deliverable

Commit on `advances` (follow scholarlm's commit-message + build-note
conventions; write the build note if that's the convention). When done,
report back:
  1. the commit SHA(s) on `advances`
  2. the exact final signature of `DocumentLM.__init__`'s new param and the
     exact name/type/shape of the coerced-labels attribute
  3. how a caller reads the coerced-label counts after `.fit()`
  4. test results (and how to run that test subset)
  5. anything you changed from the spec above and why
