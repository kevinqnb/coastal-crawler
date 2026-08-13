# coastal-crawler :octopus:

Discovery and extraction pipeline for coastal-ecosystem papers.

Papers are discovered from one or more APIs, deduplicated by DOI, screened for relevance by a two-stage filter (PDF accessibility check then LLM abstract classification), and then queued for PDF extraction via a native OCR/extraction pipeline (`src/coastal_crawler/extraction/`).

---

## Setup

```bash
uv sync
cp .env.example .env
$EDITOR .env # modify the .env file with keys and task specifications
alembic upgrade head
```

Extraction requires poppler-utils (`pdfinfo`/`pdftoppm`) to be installed on the system for PDF page rendering.

---

## Configuring discovery sources

Set `ENABLED_SOURCES` to a comma-separated list of the sources you want to use. Each source has its own required config.

### OpenAlex (default)

Requires an API key for production use. Register at `https://openalex.org/register` to obtain one. Without a key the API still responds but at a lower rate limit.

Filter papers by **topic** — find topic IDs at `https://api.openalex.org/topics` (search by name, copy the `T`-prefixed ID).

```env
ENABLED_SOURCES=openalex
OPENALEX_API_KEY=your-key-here
OPENALEX_TOPIC_IDS=T10022,T10023    # comma-separated T-prefixed IDs
```

### Semantic Scholar

No API key required, but adding one raises the rate limit from 1 to 100 req/s. Register at `https://www.semanticscholar.org/product/api`.

Filter papers by **keyword query** — the same strings you'd type into the Semantic Scholar search box.

```env
ENABLED_SOURCES=semantic_scholar
SEMANTIC_SCHOLAR_API_KEY=           # optional
SEMANTIC_SCHOLAR_QUERIES=coastal ecosystem,mangrove,salt marsh
```

### Wiley TDM

API key required. Apply at `https://onlinelibrary.wiley.com/library-info/resources/text-and-data-mining`.

Filter papers by **subject code** and/or **ISSN**. Both are optional — omitting them returns all Wiley content you have TDM access to.

```env
ENABLED_SOURCES=wiley
WILEY_API_KEY=your-key-here
WILEY_SUBJECTS=Earth and Environmental Sciences   # optional
WILEY_ISSNS=1365-2486,0028-0836                  # optional
```

### Using multiple sources together

```env
ENABLED_SOURCES=openalex,semantic_scholar,wiley
```

All three sources write into the same `papers` table. Papers that appear in more than one source are deduplicated by DOI — only one row is kept.

---

## Running the pipeline

### Discover papers

Queries all enabled sources and inserts new papers with `status='discovered'`. Safe to re-run — duplicates are silently skipped.

```bash
coastal-crawler discover

# Back-fill from a specific date (overrides the stored watermark for this run):
coastal-crawler discover --since 2024-01-01
```

Each source tracks its own watermark, so if one source fails mid-run, the others are unaffected and the failed source resumes from where it left off on the next run.

### Filter papers

After discovery, run the two-stage filter to classify papers before committing to expensive PDF extraction.

```bash
coastal-crawler filter
coastal-crawler filter --batch-size 100
```

Each paper is processed in order:

1. **No abstract** → marked `irrelevant` immediately (no network call).
2. **PDF accessibility check** → a lightweight range GET (`Range: bytes=0-1023`) is sent to the paper's open-access URL. Papers whose PDFs are unreachable (auth failure, connection error, etc.) are marked `inaccessible` and skipped. Wiley TDM URLs automatically include the API key.
3. **LLM relevance filter** → an OpenAI-compatible chat endpoint is called with `max_tokens=1`; token logprobs produce a calibrated confidence score `p_true / (p_true + p_false)` stored in `filter_confidence`. Papers where the model emits no boolean token are conservatively rejected.

Papers that pass both checks are marked `relevant` and queued for extraction.

Configure the endpoint and criteria prompt in `.env` — see the **Environment variables** table and the example prompt in `.env.example`.

### Extract papers

Claims a batch of relevant papers, downloads the open-access PDF, runs extraction, and writes results to the `extractions` table.

```bash
coastal-crawler extract
coastal-crawler extract --batch-size 20
```

Extraction uses two models: `OCRLM` (OCR) and `ExtractionLM` (single-call direct measurement extraction — one LLM call per document, no intermediate provenance/attribute-detection steps). Both are thin OpenAI-compatible clients — like the filter — so each needs its own running vLLM server (`DOC_LM_BASE_URL` / `MEAS_LM_BASE_URL`). Before running `extract`, define your entity schema and attribute catalogue in `src/coastal_crawler/measurement_schema.py` and set the `DOC_LM_*`/`MEAS_LM_*` variables in `.env` (see `.env.example`).

Multiple workers can run in parallel safely — each worker uses `SELECT ... FOR UPDATE SKIP LOCKED` so the same paper is never claimed twice.

On an HPC cluster, submit extraction as a single self-contained job that starts both vLLM servers (pinned to separate GPUs on one node), waits for both to be healthy, runs `coastal-crawler extract`, then tears both servers down:

```bash
qsub scripts/submit_extract_job.sh
```

`scripts/serve_model.sh <FILTER|DOC_LM|MEAS_LM> [gpu_id]` (used by both this and the filter job below) can also be run directly for local/interactive use.

### Retry failures

Papers that fail extraction are preserved with `status='failed'` and the error message stored. Re-queue them for extraction retry (the filter result is preserved — they go straight back to `relevant`, not re-filtered):

```bash
coastal-crawler requeue-failed
```

To rescue papers stranded in `'filtering'` by a killed job (e.g. walltime limit), then resubmit:

```bash
coastal-crawler requeue-filtering
qsub scripts/submit_filter_job.sh
```

To re-run the relevance filter on papers that were previously rejected (e.g. after updating the criteria prompt):

```bash
coastal-crawler requeue-irrelevant
```

To retry papers whose PDFs were previously unreachable (e.g. after adding a new API key or when publisher access has changed):

```bash
coastal-crawler requeue-inaccessible
```

### Running the full pipeline

```bash
coastal-crawler discover && coastal-crawler filter && coastal-crawler extract --batch-size 20
```

Or with a scheduler (example cron — discover daily, filter and extract hourly):

```cron
0 6  * * *  coastal-crawler discover
0 *  * * *  coastal-crawler filter --batch-size 100
15 * * * *  coastal-crawler extract --batch-size 20
```

---

## Data model

Two databases, two jobs:

- **Postgres** is the pipeline's operational store. Every worker's `SELECT ... FOR UPDATE SKIP LOCKED` claim and status transition (`discovered → ... → extracted`) happens against `papers`/`extractions` here — a landing zone, not something shaped for reporting.
- **DuckDB** holds a derived, read-optimized **star schema**, rebuilt from Postgres by `scripts/build_warehouse.py` (transform logic in `coastal_crawler/warehouse.py`). This is what the results site actually queries.

```mermaid
graph LR
    PG[("Postgres<br/>papers · extractions")] -- "build_warehouse.py" --> DW[("DuckDB<br/>star-schema warehouse")]
    DW --> SITE["Results site"]

    classDef pg   fill:#E4E9EF,stroke:#8a96a3,color:#0b0b0b,stroke-width:1.5px
    classDef hub  fill:#22303F,stroke:#4a6178,color:#ffffff,stroke-width:2px
    classDef site fill:#49BF95,stroke:#1baf7a,color:#0b0b0b,stroke-width:1.5px

    class PG pg
    class DW hub
    class SITE site
```

### The warehouse star schema

One `extractions_fact` row per extracted measurement, radiating out to five dimension tables — everything that would otherwise repeat across rows (a paper's title, a model's prompt version, a location's coordinates) is pulled out into its own dimension and referenced by id instead:

```mermaid
erDiagram
    extractions_fact }o--|| paper_dim      : "for paper"
    extractions_fact }o--|| model_dim      : "produced by"
    extractions_fact }o--|| entity_dim     : "measured at"
    extractions_fact }o--|| event_dim      : "during event"
    extractions_fact }o--o| qualifier_dim  : "qualified by"

    classDef hub          fill:#22303F,stroke:#4a6178,color:#ffffff,stroke-width:2px
    classDef paperDim     fill:#5593DE,stroke:#2a78d6,color:#0b0b0b,stroke-width:1.5px
    classDef modelDim     fill:#49BF95,stroke:#1baf7a,color:#0b0b0b,stroke-width:1.5px
    classDef entityDim    fill:#EF865D,stroke:#eb6834,color:#0b0b0b,stroke-width:1.5px
    classDef eventDim     fill:#6E61B9,stroke:#4a3aa7,color:#ffffff,stroke-width:1.5px
    classDef qualifierDim fill:#ED95B6,stroke:#e87ba4,color:#0b0b0b,stroke-width:1.5px

    extractions_fact:::hub {
        int      fact_id PK
        int      source_extraction_id
        int      paper_id FK
        int      extraction_model_id FK
        int      entity_id FK
        int      event_id FK
        string   attribute
        string   quantity_raw
        string   units_raw
        double   quantity_canonical
        string   units_canonical
        int      qualifier_id FK
        int      page_number
        double   confidence
    }

    paper_dim:::paperDim {
        int      paper_id PK
        string   doi
        string   title
        string   authors "list"
        date     publication_date
        string   publisher
        string   discovered_from
        string   openalex_id
        string   semantic_scholar_id
    }

    model_dim:::modelDim {
        int      model_id PK
        string   model_name
        string   role
        string   prompt_version
        int      seed
        double   temperature
    }

    entity_dim:::entityDim {
        int      entity_id PK
        double   latitude
        double   longitude
        string   name
        string   location_description
        string   identifiers
        string   ecosystem_type
        string   resolution_method
    }

    event_dim:::eventDim {
        int      event_id PK
        string   date_measured
        string   sub_location
        string   additional_details
    }

    qualifier_dim:::qualifierDim {
        int      qualifier_id PK
        string   confidence_region
        double   confidence_min
        double   confidence_max
        double   range_min
        double   range_max
        double   less_than_or_equal
        double   greater_than
    }
```

`fact_id`'s `source_extraction_id` traces each row back to Postgres's `extractions.id`. Rebuilds are full replacements (`CREATE OR REPLACE TABLE`), not incremental — see `notes/coastal-crawler/builds/2026-08-12-warehouse-init-01.md` for the design rationale (fact grain, dimension resolution, what got cut). The fact table stays a dark, unpastelled hub so it still reads as the center of the star; the five dimensions each get a distinct pastel tint (a tab10-style qualitative set — blue/aqua/orange/violet/magenta — lightened toward white, spot-checked for colorblind-safe separation and contrast).

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | yes | — | `postgresql://user:pass@host/dbname` |
| `BATCH_SIZE` | no | `10` | Papers claimed per extraction run |
| `ENABLED_SOURCES` | no | `openalex` | Comma-separated: `openalex`, `semantic_scholar`, `wiley` |
| `OPENALEX_API_KEY` | no | — | API key for higher rate limits (register at openalex.org) |
| `OPENALEX_TOPIC_IDS` | no | — | Comma-separated topic IDs, e.g. `T10022,T10023` |
| `SEMANTIC_SCHOLAR_API_KEY` | if using S2 | — | Required for bulk search endpoint |
| `SEMANTIC_SCHOLAR_QUERY` | no | — | Boolean search query (`+` AND, `\|` OR, `"phrases"`, `*` prefix) |
| `WILEY_API_KEY` | if using Wiley | — | Required to enable the Wiley source |
| `WILEY_SUBJECTS` | no | — | Comma-separated subject codes |
| `WILEY_ISSNS` | no | — | Comma-separated ISSNs to restrict queries |
| `FILTER_BASE_URL` | no | — | OpenAI-compatible endpoint base URL (e.g. `http://localhost:8000/v1`). Omit to use the OpenAI cloud API. |
| `FILTER_API_KEY` | if using filter | `EMPTY` | API key for the filter endpoint. Use `EMPTY` for local vLLM. |
| `FILTER_MODEL` | if using filter | — | Model name/path to serve (e.g. `meta-llama/Llama-3.1-8B-Instruct`) |
| `FILTER_RELEVANCE_PROMPT` | if using filter | — | System prompt describing inclusion/exclusion criteria. See `.env.example` for a full draft. |
| `DOC_LM_BASE_URL` | if using extract | — | OpenAI-compatible endpoint for the OCR/VLM model (e.g. `http://localhost:8083/v1`). |
| `DOC_LM_MODEL` | if using extract | — | OCR/VLM model name/path to serve (e.g. `allenai/olmOCR-2-7B-1025`). |
| `MEAS_LM_BASE_URL` | if using extract | — | OpenAI-compatible endpoint for the extraction LLM. |
| `MEAS_LM_MODEL` | if using extract | — | Extraction LLM model name/path to serve. |
| `MEAS_LM_ENTITY_IDENTIFICATION_PROMPT` | if using extract | — | Prompt describing what entities/measurements to identify. Entity schema/attribute catalogue live in `src/coastal_crawler/measurement_schema.py`. |
| `EXTRACTION_SCHEMA_NAME` | no | `coastal_measurement_v1` | Schema name stored on every `ExtractionResult`. |
| `EXTRACTION_LAT_FIELD` / `EXTRACTION_LON_FIELD` | no | — | `EntitySchema` field names holding coordinates, if any. |
---

## Development

```bash
# Run tests (requires a test database)
TEST_DATABASE_URL=postgresql://user:pass@localhost/crawler_test uv run --with pytest --with pytest-mock pytest

# Type-check
mypy src/

# Generate a migration after changing models
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```
