"""SQLAlchemy 2.x declarative schema for the MPEG-O MCP catalog.

Uses portable JSON and DateTime(timezone=True) columns so the same
models run against SQLite (v0.1) and Postgres (future). The column
named ``metadata_json`` (not ``metadata``) avoids shadowing the
Declarative Base attribute.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uri: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    format_version: Mapped[str] = mapped_column(String, nullable=False)
    features: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registered_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, default=1
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    studies: Mapped[list[Study]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )
    runs: Mapped[list[Run]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )
    identifications: Mapped[list[Identification]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )
    quantifications: Mapped[list[Quantification]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )
    provenance_records: Mapped[list[ProvenanceRecord]] = relationship(
        back_populates="file", cascade="all, delete-orphan", passive_deletes=True
    )


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    isa_investigation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    file: Mapped[File] = relationship(back_populates="studies")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    acquisition_mode: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    spectrum_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    instrument_manufacturer: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    instrument_model: Mapped[str | None] = mapped_column(String, nullable=True)
    polarity: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    file: Mapped[File] = relationship(back_populates="runs")
    identifications: Mapped[list[Identification]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class Identification(Base):
    __tablename__ = "identifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    chebi_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    spectrum_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    file: Mapped[File] = relationship(back_populates="identifications")
    run: Mapped[Run] = relationship(back_populates="identifications")

    __table_args__ = (
        Index("ix_identifications_chebi_score", "chebi_id", "score"),
    )


class Quantification(Base):
    __tablename__ = "quantifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    chebi_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    sample_ref: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    abundance: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    normalization_method: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    file: Mapped[File] = relationship(back_populates="quantifications")

    __table_args__ = (
        Index("ix_quantifications_chebi_sample", "chebi_id", "sample_ref"),
    )


class ProvenanceRecord(Base):
    __tablename__ = "provenance_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    software: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    input_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    output_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    file: Mapped[File] = relationship(back_populates="provenance_records")
