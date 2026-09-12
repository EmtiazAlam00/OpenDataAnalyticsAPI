"""ORM models.

Three ideas drive the shape of this schema:

1. **Nothing the publisher wrote is destroyed.** Every normalized column has a
   `*_raw` companion holding the string as published. Normalization is a view
   of the data, not a replacement for it, and a wrong rule should be fixable by
   reloading rather than by re-downloading.

2. **Re-running the loader is safe.** `(source_file, line_no)` is the natural
   key and carries a unique constraint, so a second load of the same file
   updates rows in place instead of doubling every count. A content hash would
   have been wrong here: two identical rows in one quarter are two genuinely
   separate LMIAs, and hashing would collapse them.

3. **Rows are never dropped in silence.** Anything that fails to load lands in
   `ingest_rejects` with the raw row and a reason, and the per-file tallies on
   `ingest_runs` have to add up.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class IngestRun(Base):
    """One attempt to load one file. The provenance and audit record."""

    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    source_file: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    reader: Mapped[str | None] = mapped_column(String(32))

    quarter: Mapped[str | None] = mapped_column(String(6), index=True)
    banner: Mapped[str | None] = mapped_column(Text)
    noc_version: Mapped[str | None] = mapped_column(String(4))
    header_row: Mapped[int | None] = mapped_column(Integer)

    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_loaded: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)

    # The publisher's own footnotes, captured per file. They drift between
    # quarters — the PR-only note is present in 2025Q2 and gone by 2025Q3 —
    # so they are stored rather than hardcoded, and /about serves them verbatim.
    notes: Mapped[list | None] = mapped_column(JSONB)

    # Counters for values that normalized to nothing: unrecognised province or
    # stream labels, unparseable counts, truncated postal codes. These do not
    # reject a row, but an unexplained jump in them means a rule has gone stale.
    anomalies: Mapped[dict | None] = mapped_column(JSONB)
    unmapped_columns: Mapped[list | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    error: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rejects: Mapped[list[IngestReject]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class IngestReject(Base):
    """A row that could not be loaded, kept with enough context to diagnose it."""

    __tablename__ = "ingest_rejects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ingest_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingest_runs.id", ondelete="CASCADE"), index=True
    )
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    raw_row: Mapped[dict | None] = mapped_column(JSONB)

    run: Mapped[IngestRun] = relationship(back_populates="rejects")


class EmployerEntity(Base):
    """A group of raw employer spellings judged to be the same employer.

    `match_method` records how the grouping was arrived at. Today every row is
    'deterministic' — rule-based normalization plus exact matching on the
    resulting key, which cannot merge two genuinely different companies. A
    later pg_trgm similarity pass can add rows marked 'fuzzy' without a
    migration, and consumers can filter on it.
    """

    __tablename__ = "employer_entities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employer_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    match_method: Mapped[str] = mapped_column(String(16), default="deterministic")

    variant_count: Mapped[int] = mapped_column(Integer, default=0)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_quarter: Mapped[str | None] = mapped_column(String(6))
    last_seen_quarter: Mapped[str | None] = mapped_column(String(6))

    rows: Mapped[list[EmployerLMIA]] = relationship(back_populates="entity")

    __table_args__ = (
        # Serves ILIKE '%…%' search on the canonical name as well as the
        # similarity comparisons a future fuzzy pass will need.
        Index(
            "ix_employer_entities_name_trgm",
            "canonical_name",
            postgresql_using="gin",
            postgresql_ops={"canonical_name": "gin_trgm_ops"},
        ),
    )


class EmployerLMIA(Base):
    """One published row: an employer, an occupation, a location, a quarter."""

    __tablename__ = "employers_lmia"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # --- provenance -------------------------------------------------------
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    ingest_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingest_runs.id", ondelete="SET NULL")
    )
    quarter: Mapped[str] = mapped_column(String(6), nullable=False, index=True)

    # --- employer ---------------------------------------------------------
    employer_raw: Mapped[str] = mapped_column(Text, nullable=False)
    # The legal entity: who the LMIA was issued to. A franchisee is not its
    # franchisor, so '1317518 Alberta Ltd. o/a Tim Hortons' keys on the
    # numbered company, not on the brand.
    employer_key: Mapped[str | None] = mapped_column(Text, index=True)
    employer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employer_entities.id", ondelete="SET NULL"), index=True
    )
    # The brand it trades as, where one is declared. Thirteen separate
    # companies operate Tim Hortons outlets in the 2025-26 files; grouping by
    # this answers "how many positions across Tim Hortons", which is a
    # different and equally reasonable question from "which single employer
    # was approved for the most".
    trade_name_raw: Mapped[str | None] = mapped_column(Text)
    brand_key: Mapped[str | None] = mapped_column(Text, index=True)

    # --- location ---------------------------------------------------------
    province_raw: Mapped[str | None] = mapped_column(Text)
    province: Mapped[str | None] = mapped_column(String(2), index=True)
    # 'province' | 'outside_canada' | 'unknown'. Kept distinct from a NULL
    # province so that "employers headquartered outside Canada" — which the
    # source files put in the province column — never appears in a per-province
    # breakdown as though it were a fourteenth region.
    location_scope: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    address_raw: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(String(7))
    postal_fsa: Mapped[str | None] = mapped_column(String(3), index=True)
    postal_truncated: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- program ----------------------------------------------------------
    program_stream_raw: Mapped[str | None] = mapped_column(Text)
    program_stream: Mapped[str | None] = mapped_column(Text, index=True)
    # Denormalized from program_stream so a time series can be put on a
    # like-for-like basis with an indexed predicate rather than a string match.
    is_pr_only: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    incorporate_status: Mapped[str | None] = mapped_column(Text, index=True)

    # --- occupation -------------------------------------------------------
    occupation_raw: Mapped[str | None] = mapped_column(Text)
    noc_code: Mapped[str | None] = mapped_column(String(8), index=True)
    noc_title: Mapped[str | None] = mapped_column(Text)
    # '2011' | '2021'. Taken from the file's own banner, which is authoritative:
    # ESDC retroactively converted pre-September-2024 occupations to NOC 2021
    # using StatCan concordance, so the vintage is a property of the file's
    # publication date rather than of the quarter it describes.
    noc_version: Mapped[str | None] = mapped_column(String(4), index=True)

    # --- counts -----------------------------------------------------------
    # Two distinct metrics. One LMIA can authorise several positions, so these
    # answer different questions and neither substitutes for the other.
    approved_lmias: Mapped[int | None] = mapped_column(Integer)
    approved_lmias_raw: Mapped[str | None] = mapped_column(Text)
    approved_positions: Mapped[int | None] = mapped_column(Integer)
    approved_positions_raw: Mapped[str | None] = mapped_column(Text)

    entity: Mapped[EmployerEntity | None] = relationship(back_populates="rows")

    __table_args__ = (
        # The idempotency key. A reload of the same file updates in place.
        UniqueConstraint("source_file", "line_no", name="uq_employers_lmia_source_line"),
        # Substring search on employer names — `?q=tim hortons` — without a
        # sequential scan over 45k+ rows.
        Index(
            "ix_employers_lmia_employer_trgm",
            "employer_raw",
            postgresql_using="gin",
            postgresql_ops={"employer_raw": "gin_trgm_ops"},
        ),
        # The shape of nearly every /stats/* aggregation: filter by quarter,
        # group by one dimension.
        Index("ix_employers_lmia_quarter_province", "quarter", "province"),
        Index("ix_employers_lmia_quarter_noc", "quarter", "noc_code"),
        Index("ix_employers_lmia_quarter_stream", "quarter", "program_stream"),
    )
