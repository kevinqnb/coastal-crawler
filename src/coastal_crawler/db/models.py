"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    REAL,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (Index("ix_papers_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doi: Mapped[str | None] = mapped_column(String, unique=True)
    # Source-specific IDs — all nullable because a paper may arrive from any single source.
    # doi is the primary cross-source dedup key; these are fallbacks for DOI-less records.
    openalex_id: Mapped[str | None] = mapped_column(String, unique=True)
    semantic_scholar_id: Mapped[str | None] = mapped_column(String, unique=True)
    title: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    discovered_from: Mapped[str | None] = mapped_column(String)
    oa_pdf_url: Mapped[str | None] = mapped_column(Text)
    # Column is named "metadata" in the DB; "paper_metadata" avoids shadowing
    # DeclarativeBase.metadata on the class.
    paper_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="discovered", server_default="discovered"
    )
    discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    extractions: Mapped[list[Extraction]] = relationship(
        "Extraction", back_populates="paper", cascade="all, delete-orphan"
    )


class Extraction(Base):
    __tablename__ = "extractions"
    __table_args__ = (Index("ix_extractions_paper_id", "paper_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), nullable=False)
    schema_name: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(REAL)
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    latitude: Mapped[float | None] = mapped_column(Double)
    longitude: Mapped[float | None] = mapped_column(Double)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    paper: Mapped[Paper] = relationship("Paper", back_populates="extractions")


class CrawlState(Base):
    """One row per discovery source — each source tracks its own watermark."""

    __tablename__ = "crawl_state"

    source: Mapped[str] = mapped_column(String, primary_key=True)
    watermark: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
