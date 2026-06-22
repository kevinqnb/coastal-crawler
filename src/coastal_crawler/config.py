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
