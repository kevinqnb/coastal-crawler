"""Application settings loaded from environment variables / .env file."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


def _decode_csv(value: Any) -> Any:
    """Parse a list field value from either JSON array or comma-separated string.

    pydantic-settings calls decode_complex_value() for every list/set field
    before pydantic validators run.  The default implementation calls
    json.loads(), which rejects plain CSV strings like ``openalex,wiley``.
    This function accepts both formats so users don't have to JSON-encode
    their .env values.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith(("[", "{")):
        return json.loads(stripped)
    return [x.strip() for x in stripped.split(",") if x.strip()]


class _CsvEnvSource(EnvSettingsSource):
    def decode_complex_value(self, field_name: str, field: Any, value: Any) -> Any:
        return _decode_csv(value)


class _CsvDotEnvSource(DotEnvSettingsSource):
    def decode_complex_value(self, field_name: str, field: Any, value: Any) -> Any:
        return _decode_csv(value)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ core
    database_url: str = Field(description="PostgreSQL connection URL")
    batch_size: int = Field(default=10, description="Papers claimed per extraction run")

    # ------------------------------------------------------------ sources
    enabled_sources: list[str] = Field(
        default_factory=lambda: ["openalex"],
        description="Discovery sources to query: openalex, semantic_scholar, wiley",
    )

    # ---------------------------------------------------------- OpenAlex
    openalex_api_key: str | None = Field(
        default=None,
        description="OpenAlex API key (optional, increases rate limits)",
    )
    openalex_topic_ids: list[str] = Field(
        default_factory=list,
        description="Comma-separated OpenAlex topic IDs (T-prefixed)",
    )

    # ------------------------------------------------- Semantic Scholar
    semantic_scholar_api_key: str | None = Field(
        default=None,
        description="API key for Semantic Scholar bulk search (required)",
    )
    semantic_scholar_query: str | None = Field(
        default=None,
        description="Boolean search query for Semantic Scholar bulk search",
    )

    # -------------------------------------------------- Abstract filter
    filter_base_url: str | None = Field(
        default=None,
        description="Base URL for the OpenAI-compatible LLM endpoint (e.g. vLLM). None = OpenAI cloud.",
    )
    filter_api_key: str = Field(
        default="EMPTY",
        description="API key for the filter LLM endpoint. Use 'EMPTY' for local vLLM servers.",
    )
    filter_model: str | None = Field(
        default=None,
        description="Model name to use for abstract relevance filtering.",
    )
    filter_relevance_prompt: str | None = Field(
        default=None,
        description="System prompt describing relevance criteria. Model responds true/false.",
    )

    # Inference parameters — passed to every API call and should match the
    # values used when the server was launched for full reproducibility.
    filter_seed: int = Field(
        default=0,
        description="RNG seed passed to the API (and to vLLM --seed). Set both to the same value.",
    )
    filter_temperature: float = Field(
        default=0.0,
        description="Sampling temperature. 0.0 = greedy decoding (recommended for classification).",
    )
    filter_top_logprobs: int = Field(
        default=20,
        description="Number of top token logprobs to request. Must be high enough to capture true/false variants.",
    )

    # Serving parameters — used only by scripts/serve_model.sh FILTER.
    # Stored here so they are tracked alongside inference params.
    filter_port: int = Field(
        default=8000,
        description="Port vLLM listens on. Must match the port in FILTER_BASE_URL.",
    )
    filter_tensor_parallel_size: int = Field(
        default=1,
        description="Number of GPUs for tensor parallelism (vLLM --tensor-parallel-size).",
    )
    filter_gpu_memory_utilization: float = Field(
        default=0.90,
        description="Fraction of GPU memory vLLM may use (vLLM --gpu-memory-utilization).",
    )
    filter_dtype: str = Field(
        default="auto",
        description="Compute dtype: auto, bfloat16, float16, float32 (vLLM --dtype).",
    )
    filter_quantization: str | None = Field(
        default=None,
        description="Quantization scheme: awq, gptq, fp8, etc. None = no quantization (vLLM --quantization).",
    )
    filter_max_model_len: int | None = Field(
        default=None,
        description="Override the model's maximum context length (vLLM --max-model-len). None = model default.",
    )
    filter_sif_path: str | None = Field(
        default=None,
        description="Path to a vLLM Singularity .sif image. If set, scripts/serve_model.sh FILTER runs inside the container.",
    )
    filter_batch_size: int = Field(
        default=50,
        description="Papers claimed per filter run.",
    )

    # ------------------------------------------------ Document LM (OCR/VLM)
    doc_lm_base_url: str | None = Field(
        default=None,
        description="Base URL for the OCR/VLM OpenAI-compatible endpoint (e.g. vLLM).",
    )
    doc_lm_api_key: str = Field(
        default="EMPTY",
        description="API key for the OCRLM endpoint. Use 'EMPTY' for local vLLM servers.",
    )
    doc_lm_model: str | None = Field(
        default=None,
        description="Model name to use for OCR (served via vLLM OpenAI-compatible endpoint).",
    )
    doc_lm_seed: int = Field(
        default=0,
        description="RNG seed passed to vLLM --seed for the OCRLM server.",
    )
    doc_lm_max_concurrent: int = Field(
        default=32,
        description="Maximum concurrent OCR page-image API calls the client sends at once (client-side concurrency, not a vLLM server flag).",
    )
    doc_lm_unknown_label_policy: str = Field(
        default="raise",
        description=(
            "scholarlm DocumentLM's handling of a chandra-ocr-2 data-label "
            "outside its audited set: 'raise' fails the document (default); "
            "'coerce' classifies the region by HTML shape (table/figure/text) "
            "instead and records it in DocumentLM.coerced_labels, logged by "
            "DirectOCRAdapter.ocr_batch as 'ocr_unknown_labels_coerced'."
        ),
    )

    # Serving parameters — used only by scripts/serve_model.sh DOC_LM.
    doc_lm_port: int = Field(
        default=8083,
        description="Port vLLM listens on for OCRLM. Must match the port in DOC_LM_BASE_URL.",
    )
    doc_lm_tensor_parallel_size: int = Field(
        default=1,
        description="Number of GPUs for tensor parallelism (vLLM --tensor-parallel-size).",
    )
    doc_lm_gpu_memory_utilization: float = Field(
        default=0.90,
        description="Fraction of GPU memory vLLM may use (vLLM --gpu-memory-utilization).",
    )
    doc_lm_dtype: str = Field(
        default="auto",
        description="Compute dtype: auto, bfloat16, float16, float32 (vLLM --dtype).",
    )
    doc_lm_quantization: str | None = Field(
        default=None,
        description="Quantization scheme: awq, gptq, fp8, etc. None = no quantization (vLLM --quantization).",
    )
    doc_lm_max_model_len: int | None = Field(
        default=None,
        description="Override the model's maximum context length (vLLM --max-model-len). None = model default.",
    )
    doc_lm_sif_path: str | None = Field(
        default=None,
        description="Path to a vLLM Singularity .sif image. If set, scripts/serve_model.sh runs OCRLM inside the container.",
    )

    # --------------------------------------- Measurement LM (extraction)
    meas_lm_base_url: str | None = Field(
        default=None,
        description="Base URL for the extraction-LLM OpenAI-compatible endpoint (e.g. vLLM).",
    )
    meas_lm_api_key: str = Field(
        default="EMPTY",
        description="API key for the ExtractionLM endpoint. Use 'EMPTY' for local vLLM servers.",
    )
    meas_lm_model: str | None = Field(
        default=None,
        description="Model name to use for measurement extraction (served via vLLM OpenAI-compatible endpoint).",
    )
    meas_lm_entity_identification_prompt: str | None = Field(
        default=None,
        description="Prompt describing the entities/measurements to identify in each paper.",
    )
    meas_lm_seed: int = Field(
        default=0,
        description="RNG seed passed to vLLM --seed for the ExtractionLM server.",
    )
    meas_lm_max_concurrent: int = Field(
        default=4,
        description="Maximum concurrent extraction API calls the client sends at once (client-side concurrency, not a vLLM server flag). Was hardcoded to 1.",
    )
    meas_lm_temperature: float = Field(
        default=0.90,
        description=(
            "Sampling temperature passed to scholarlm.MeasurementLM's sampling_params. "
            "Must always be passed explicitly (never omit sampling_params on construction) — "
            "MeasurementLM's default-arg handling silently drops all sampling params if the "
            "kwarg is omitted. Default matches scholarlm's own documented default."
        ),
    )
    meas_lm_top_p: float = Field(
        default=0.95,
        description="Nucleus sampling top-p passed to scholarlm.MeasurementLM's sampling_params.",
    )
    meas_lm_top_k: int = Field(
        default=64,
        description="Top-k sampling passed to scholarlm.MeasurementLM's sampling_params (forwarded via extra_body).",
    )
    meas_lm_repetition_penalty: float = Field(
        default=1.0,
        description="Repetition penalty passed to scholarlm.MeasurementLM's sampling_params (forwarded via extra_body).",
    )
    meas_lm_enable_thinking: bool = Field(
        default=False,
        description="Whether to enable chain-of-thought/reasoning mode, passed to scholarlm.MeasurementLM's sampling_params (forwarded via extra_body's chat_template_kwargs).",
    )

    # Serving parameters — used only by scripts/serve_model.sh MEAS_LM.
    meas_lm_port: int = Field(
        default=8084,
        description="Port vLLM listens on for ExtractionLM. Must match the port in MEAS_LM_BASE_URL.",
    )
    meas_lm_tensor_parallel_size: int = Field(
        default=1,
        description="Number of GPUs for tensor parallelism (vLLM --tensor-parallel-size).",
    )
    meas_lm_gpu_memory_utilization: float = Field(
        default=0.90,
        description="Fraction of GPU memory vLLM may use (vLLM --gpu-memory-utilization).",
    )
    meas_lm_dtype: str = Field(
        default="auto",
        description="Compute dtype: auto, bfloat16, float16, float32 (vLLM --dtype).",
    )
    meas_lm_quantization: str | None = Field(
        default=None,
        description="Quantization scheme: awq, gptq, fp8, etc. None = no quantization (vLLM --quantization).",
    )
    meas_lm_max_model_len: int | None = Field(
        default=None,
        description="Override the model's maximum context length (vLLM --max-model-len). None = model default.",
    )
    meas_lm_sif_path: str | None = Field(
        default=None,
        description="Path to a vLLM Singularity .sif image. If set, scripts/serve_model.sh runs ExtractionLM inside the container.",
    )

    # -------------------------------------------------------- Judge/attribution
    # No port/base_url/serving params here — JudgementLM loads weights
    # directly in-process via nnsight (no vLLM server), unlike FILTER/
    # DOC_LM/MEAS_LM. See judge_worker.py and
    # notes/coastal-crawler/builds/2026-08-18-judgement-attribution-01.md.
    judge_instructions_prompt: str | None = Field(
        default=None,
        description=(
            "System-role instructions describing how to judge whether an "
            "extracted measurement is valid, given its source snippet as "
            "context. Mirrors FILTER_RELEVANCE_PROMPT/"
            "MEAS_LM_ENTITY_IDENTIFICATION_PROMPT's role — a static prompt "
            "the user supplies; the per-extraction query (attribute/value/"
            "units) is built programmatically by judge_worker.py, not "
            "configured here."
        ),
    )
    judge_model: str | None = Field(
        default=None,
        description=(
            "HuggingFace model id for the judgement LLM (e.g. "
            "Qwen/Qwen2.5-7B-Instruct), passed directly to "
            "scholarlm.JudgementLM. Not a scholarlm INTERP_JUDGE_REGISTRY "
            "key — that registry lives outside scholarlm's installed "
            "package (experiments/, not src/scholarlm/) and isn't importable "
            "here."
        ),
    )
    judge_dtype: str = Field(
        default="bfloat16",
        description=(
            "torch dtype name (e.g. bfloat16, float16, float32) passed to "
            "JudgementLM's nnsight_kwargs as torch_dtype."
        ),
    )
    judge_max_new_tokens: int = Field(
        default=1,
        description=(
            "Passed to JudgementLM's sampling_params. Must stay 1 for "
            "ProbeAttribution's construction-time assertion to hold (the "
            "probe was trained on a single prefill forward pass) — exposed "
            "as a setting rather than hardcoded per CLAUDE.md's no-magic-"
            "numbers rule, not because it's expected to change."
        ),
    )
    judge_use_chat_template: bool = Field(
        default=True,
        description=(
            "Whether to wrap the (instructions, context, query) prompt in "
            "the tokenizer's chat template. True for instruction-tuned "
            "judge models (the default here); base models trained without "
            "one should set this False."
        ),
    )
    judge_seed: int = Field(
        default=0,
        description="RNG seed. JudgementLM's sampling_params use do_sample=False (greedy) by default, so this mainly documents intended reproducibility rather than affecting output.",
    )
    judge_probe_path: str | None = Field(
        default=None,
        description=(
            "Absolute path to a trained head probe pickle (joblib), e.g. "
            "scholarlm's data/experiments/pond/synthetic_probe/"
            "qwen-2.5-7b/trained_probe/head_probe_noplatt.pkl — must be the "
            "no-Platt ('_noplatt') variant, a bare sklearn Pipeline, per "
            "attribution.ProbeAttribution's assertion. Loaded directly via "
            "joblib.load(); scholarlm's analysis.loaders.load_trained_probe "
            "path-construction helper isn't used since analysis/ isn't part "
            "of the installed scholarlm package."
        ),
    )
    judge_probe_model_key: str | None = Field(
        default=None,
        description=(
            "Expected value of the loaded probe pickle's own 'judge_model' "
            "field. adapter.build_judge() asserts the two match, so this "
            "catches JUDGE_PROBE_PATH silently pointing at the wrong pickle "
            "file (fail loud rather than compute attribution against a "
            "probe you didn't intend). It is NOT the same string as "
            "JUDGE_MODEL and does NOT by itself guarantee the probe is "
            "trained for whatever model JUDGE_MODEL currently names: "
            "scholarlm's trained-probe artifacts self-identify with the "
            "short scholarlm-internal registry key they were trained under "
            "(e.g. 'qwen-2.5-7b'), not the full HuggingFace model id (e.g. "
            "'Qwen/Qwen2.5-7B-Instruct') — confirmed by directly loading "
            "head_probe_noplatt.pkl (see notes/coastal-crawler/builds/"
            "2026-08-18-judgement-attribution-01.md, Stage 1 item 2) — and "
            "scholarlm's registry itself isn't importable here (see "
            "JUDGE_MODEL's description), so there is no way to derive one "
            "from the other automatically. Keeping this value correct when "
            "either JUDGE_MODEL or JUDGE_PROBE_PATH changes is the "
            "operator's responsibility; this field only catches drift "
            "against itself, not against JUDGE_MODEL."
        ),
    )
    judge_batch_size: int = Field(
        default=10,
        description="Extraction rows claimed per judge run.",
    )

    # ------------------------------------------------------- Extraction
    extraction_schema_name: str = Field(
        default="coastal_measurement_v1",
        description="Schema name stored on every ExtractionResult (see measurement_schema.py).",
    )
    extraction_model_version: str | None = Field(
        default=None,
        description="Free-form version tag stored on every ExtractionResult. Defaults to a value derived from doc_lm_model/meas_lm_model if unset.",
    )
    extraction_lat_field: str | None = Field(
        default=None,
        description="Name of the EntitySchema field holding latitude, if your schema has coordinates (see measurement_schema.py). None = no coordinates.",
    )
    extraction_lon_field: str | None = Field(
        default=None,
        description="Name of the EntitySchema field holding longitude, if your schema has coordinates (see measurement_schema.py). None = no coordinates.",
    )
    extraction_chunk_size: int = Field(
        default=20,
        description=(
            "Papers processed per ExtractionLM GPU call within one claimed 'extract' "
            "batch. Independent of ocr_chunk_size — the extraction stage reads OCR "
            "text from disk (no network/download overlap to hide)."
        ),
    )

    # ------------------------------------------------------------- OCR
    ocr_dir: str = Field(
        default="data/ocr",
        description=(
            "Directory the OCR stage writes each paper's OCR text into "
            "(named {paper_id}.txt). The extraction stage reads from here "
            "instead of re-running OCR, so the two stages can run as "
            "separate jobs/processes."
        ),
    )
    ocr_chunk_size: int = Field(
        default=20,
        description=(
            "Papers processed per OCRLM GPU call within one claimed 'ocr' batch. "
            "Downloads for the next chunk run in a background thread while the "
            "current chunk's GPU work runs, hiding PDF download / Wiley-throttle "
            "wait time behind GPU compute. Kept independent of "
            "extraction_chunk_size since the two stages have different GPU "
            "concurrency profiles (see doc_lm_max_concurrent vs meas_lm_max_concurrent)."
        ),
    )

    # ---------------------------------------------- Location resolution
    location_distance_threshold_km: float = Field(
        default=1.0,
        description=(
            "Max great-circle (haversine) distance in km between two "
            "coordinate-bearing extraction points for scripts/build_warehouse.py's "
            "entity resolution to cluster them into the same entity."
        ),
    )
    location_name_similarity_threshold: float = Field(
        default=0.85,
        description=(
            "Min difflib.SequenceMatcher.ratio() (0-1) between two normalized "
            "site names for scripts/build_warehouse.py's entity resolution to merge "
            "coordinate-less extraction rows into the same entity."
        ),
    )

    # ---------------------------------------------------------- Warehouse
    warehouse_path: str = Field(
        default="data/warehouse.duckdb",
        description=(
            "Output path for scripts/build_warehouse.py's DuckDB star-schema "
            "warehouse file. Rebuilt atomically (written to a temp path, then "
            "renamed into place) on every run."
        ),
    )

    # ------------------------------------------------------- Wiley TDM
    wiley_api_key: str | None = Field(
        default=None,
        description="Wiley TDM API key (required to enable the Wiley source)",
    )
    wiley_subjects: list[str] = Field(
        default_factory=list,
        description="Comma-separated Wiley subject codes to filter by",
    )
    wiley_issns: list[str] = Field(
        default_factory=list,
        description="Comma-separated journal ISSNs to restrict Wiley queries to",
    )
    wiley_pdf_dir: str = Field(
        default="data/wiley_pdfs",
        description=(
            "Directory scripts/wiley_download.py pre-downloads Wiley PDFs into "
            "(named {paper_id}.pdf). The extraction worker reads Wiley papers' "
            "PDFs from here instead of downloading them live, so only one "
            "process ever talks to Wiley's rate-limited TDM API — this is what "
            "makes it safe to run multiple `coastal-crawler extract` jobs in "
            "parallel. See EFFICIENCY.md item 1."
        ),
    )

    # field_validator handles CSV strings passed directly to the constructor
    # (init_settings path). The custom sources below handle dotenv/env paths.
    @field_validator(
        "enabled_sources",
        "openalex_topic_ids",
        "wiley_subjects",
        "wiley_issns",
        mode="before",
    )
    @classmethod
    def _parse_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @field_validator(
        "filter_max_model_len",
        "filter_quantization",
        "filter_sif_path",
        "doc_lm_max_model_len",
        "doc_lm_quantization",
        "doc_lm_sif_path",
        "meas_lm_max_model_len",
        "meas_lm_quantization",
        "meas_lm_sif_path",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        **kwargs: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Replace both env sources with CSV-aware subclasses.
        # _CsvDotEnvSource(settings_cls) picks up env_file from model_config.
        # kwargs absorbs the secrets source (renamed across pydantic-settings versions).
        return (
            init_settings,
            _CsvEnvSource(settings_cls),
            _CsvDotEnvSource(settings_cls),
            *kwargs.values(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
