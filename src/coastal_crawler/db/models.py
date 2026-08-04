"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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
    authors: Mapped[list[str] | None] = mapped_column(JSONB)
    publication_date: Mapped[date | None] = mapped_column(Date)
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
    filter_confidence: Mapped[float | None] = mapped_column(REAL)
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
    # Majority vote outcome ('valid'/'invalid'), recomputed from `votes` on
    # every new vote. NULL until at least one vote is cast.
    judgement: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    paper: Mapped[Paper] = relationship("Paper", back_populates="extractions")
    votes: Mapped[list[Vote]] = relationship(
        "Vote", back_populates="extraction", cascade="all, delete-orphan"
    )


class PaperOcrContext(Base):
    """One paper's full OCR'd text — one row per paper, not per extraction.

    ExtractionLM previously had every measurement record it produced embed
    its own copy of the source document's full OCR text (`data->'context'`
    on `Extraction`), so a paper with N measurements stored that text N
    times. This table holds exactly one copy per paper instead; the site's
    snippet lookup (site/snippets.py's find_snippet) reads from here via
    paper_id rather than from any single extraction row's `data`.
    """

    __tablename__ = "paper_ocr_context"

    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("papers.id"), primary_key=True)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Vote(Base):
    """One site visitor's valid/invalid vote on an extracted measurement."""

    __tablename__ = "votes"
    __table_args__ = (Index("ix_votes_extraction_id", "extraction_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extraction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("extractions.id"), nullable=False
    )
    vote: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Hashed IP+UA — best-effort duplicate-vote deterrent, not authentication.
    voter_hash: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    extraction: Mapped[Extraction] = relationship("Extraction", back_populates="votes")


class CrawlState(Base):
    """One row per discovery source — each source tracks its own watermark."""

    __tablename__ = "crawl_state"

    source: Mapped[str] = mapped_column(String, primary_key=True)
    watermark: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
