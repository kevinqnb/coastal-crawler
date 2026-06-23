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

    # Serving parameters — used only by scripts/serve_filter_model.sh.
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
        description="Path to a vLLM Singularity .sif image. If set, scripts/serve_filter_model.sh runs inside the container.",
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
