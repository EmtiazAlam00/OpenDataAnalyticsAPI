"""The loader.

Contract, in order of importance:

1. **Re-running is a no-op.** Rows are upserted on `(source_file, line_no)` and
   stamped with the current run id; anything left over from a previous run of
   the same file is deleted afterwards. Loading the same file twice leaves the
   database exactly as it was after the first time, including when the file has
   since had rows removed.

2. **Nothing disappears quietly.** A row that cannot be loaded goes to
   `ingest_rejects` with its raw contents and a reason. A *value* that cannot
   be normalized does not reject the row — it becomes NULL beside its
   preserved `*_raw` string, and increments a counter on the run. Losing a
   province is a hole in one column; losing the row is a hole in every column.

3. **The tallies have to add up.** `rows_read == rows_loaded + rows_rejected`
   for every run, and `tests/ingest/` asserts it.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models import EmployerEntity, EmployerLMIA, IngestReject, IngestRun
from ingest import discover, headers
from ingest.discover import SourceFile
from ingest.normalize import address as address_norm
from ingest.normalize import employer as employer_norm
from ingest.normalize import numbers, occupation, province, stream
from ingest.normalize.text import clean

log = logging.getLogger(__name__)

# Rows per executemany batch. Large enough that round trips stop mattering,
# small enough that a failure does not roll back an hour of work.
BATCH_SIZE = 2_000

# Distinct unrecognised values retained per anomaly type, for the ingest report.
SAMPLE_LIMIT = 12


@dataclass
class RowOutcome:
    values: dict[str, Any] | None = None
    reject_reason: str | None = None
    reject_detail: str | None = None


@dataclass
class Tally:
    counts: Counter = field(default_factory=Counter)
    samples: dict[str, set[str]] = field(default_factory=dict)

    def note(self, kind: str, value: str | None = None) -> None:
        self.counts[kind] += 1
        if value:
            bucket = self.samples.setdefault(kind, set())
            if len(bucket) < SAMPLE_LIMIT:
                bucket.add(value)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = dict(self.counts)
        for kind, values in self.samples.items():
            out[f"{kind}_samples"] = sorted(values)
        return out


def _build_row(
    raw: pd.Series,
    line_no: int,
    column_map: headers.ColumnMap,
    quarter: str,
    noc_version: str | None,
    source_file: str,
    tally: Tally,
) -> RowOutcome:
    """Normalize one sheet row into column values, or explain why it cannot be."""

    def cell(field_name: str) -> Any:
        index = column_map.index_of(field_name)
        return raw.iloc[index] if index is not None and index < len(raw) else None

    employer_raw = clean(cell(headers.EMPLOYER))
    if employer_raw is None:
        return RowOutcome(reject_reason="missing_employer", reject_detail="employer cell is empty")

    employer_key = employer_norm.make_key(employer_raw)
    if employer_key is None:
        tally.note("employer_key_unresolved", employer_raw)
    _legal, trade_name = employer_norm.split_trade_name(employer_raw)
    brand_key = employer_norm.make_brand_key(employer_raw)
    if trade_name:
        tally.note("trade_name_extracted")

    province_raw = clean(cell(headers.PROVINCE))
    province_code, location_scope = province.normalize(province_raw)
    if province_code is None and location_scope == province.SCOPE_UNKNOWN and province_raw:
        tally.note("province_unmapped", province_raw)

    stream_raw = clean(cell(headers.PROGRAM_STREAM))
    stream_canonical = stream.normalize(stream_raw)
    if stream_canonical is None and stream_raw:
        tally.note("stream_unmapped", stream_raw)

    # A file that splits NOC across two columns wins over one that packs it
    # into the occupation text; older quarters are expected to do the former.
    if column_map.has_split_noc:
        noc_code = clean(cell(headers.NOC_CODE))
        noc_title = clean(cell(headers.NOC_TITLE)) or clean(cell(headers.OCCUPATION))
        occupation_raw = clean(cell(headers.OCCUPATION)) or noc_title
    else:
        occupation_raw = clean(cell(headers.OCCUPATION))
        noc_code, noc_title = occupation.split(occupation_raw)
        if occupation_raw and noc_code is None:
            tally.note("occupation_unpacked", occupation_raw)

    addr = address_norm.parse(cell(headers.ADDRESS))
    if addr.postal_truncated:
        tally.note("postal_truncated")
    # The address carries its own province abbreviation. Where the two columns
    # disagree, neither is silently preferred — the discrepancy is counted.
    if addr.province_code and province_code and addr.province_code != province_code:
        tally.note("province_conflict", f"{province_code}≠{addr.province_code}")

    lmias, lmias_raw, lmias_err = numbers.parse_int(cell(headers.APPROVED_LMIAS))
    positions, positions_raw, positions_err = numbers.parse_int(cell(headers.APPROVED_POSITIONS))
    if lmias_err:
        tally.note(f"approved_lmias_{lmias_err}", lmias_raw)
    if positions_err:
        tally.note(f"approved_positions_{positions_err}", positions_raw)

    return RowOutcome(
        values={
            "source_file": source_file,
            "line_no": line_no,
            "quarter": quarter,
            "employer_raw": employer_raw,
            "employer_key": employer_key,
            "trade_name_raw": trade_name,
            "brand_key": brand_key,
            "province_raw": province_raw,
            "province": province_code,
            "location_scope": location_scope,
            "address_raw": addr.raw,
            "city": addr.city,
            "postal_code": addr.postal_code,
            "postal_fsa": addr.postal_fsa,
            "postal_truncated": addr.postal_truncated,
            "program_stream_raw": stream_raw,
            "program_stream": stream_canonical,
            "is_pr_only": stream.is_pr_only(stream_canonical),
            "incorporate_status": clean(cell(headers.INCORPORATE_STATUS)),
            "occupation_raw": occupation_raw,
            "noc_code": noc_code,
            "noc_title": noc_title,
            "noc_version": occupation.infer_version(noc_code, noc_version),
            "approved_lmias": lmias,
            "approved_lmias_raw": lmias_raw if lmias is None else None,
            "approved_positions": positions,
            "approved_positions_raw": positions_raw if positions is None else None,
        }
    )


def _upsert(session: Session, rows: list[dict[str, Any]], run_id: int) -> int:
    """Insert or update on the (source_file, line_no) natural key.

    Batched `ON CONFLICT DO UPDATE` rather than `COPY`: the upsert semantics are
    the requirement here, and at this volume the round trips are not the cost.
    A COPY into an unlogged staging table feeding one INSERT … SELECT is the
    next step if the archive turns out to be an order of magnitude larger.
    """
    if not rows:
        return 0

    updatable = [c.name for c in EmployerLMIA.__table__.columns if c.name not in {"id"}]
    written = 0

    for start in range(0, len(rows), BATCH_SIZE):
        batch = [{**row, "ingest_run_id": run_id} for row in rows[start : start + BATCH_SIZE]]
        statement = insert(EmployerLMIA).values(batch)
        statement = statement.on_conflict_do_update(
            constraint="uq_employers_lmia_source_line",
            set_={name: statement.excluded[name] for name in updatable},
        )
        session.execute(statement)
        written += len(batch)

    return written


def load_file(session: Session, src: SourceFile) -> IngestRun:
    """Load one quarterly file. Idempotent: safe to call repeatedly."""
    path = src.path
    run = IngestRun(
        source_file=path.name,
        file_sha256=discover.sha256(path),
        file_bytes=path.stat().st_size,
        file_format=src.fmt,
        quarter=src.quarter,
        status="running",
    )
    session.add(run)
    session.flush()

    try:
        frame, reader = discover.read_raw(path, src.fmt)
        run.reader = reader

        layout = discover.detect_layout(frame)
        run.banner = layout.banner
        run.noc_version = layout.noc_version
        run.header_row = layout.header_row
        run.notes = layout.notes

        quarter = src.quarter or discover.quarter_from_banner(layout.banner)
        if quarter is None:
            raise ValueError(
                f"cannot determine the quarter for {path.name} "
                "from either the filename or the title banner"
            )
        run.quarter = quarter

        column_map = headers.map_columns(layout.columns)
        run.unmapped_columns = [{"index": i, "header": h} for i, h in column_map.unmapped]
        if not column_map.ok:
            raise ValueError(
                f"{path.name} is missing required column(s): "
                f"{', '.join(column_map.missing_required)}"
            )

        tally = Tally()
        rows: list[dict[str, Any]] = []
        rejects: list[IngestReject] = []

        for line_no, raw in layout.data.iterrows():
            outcome = _build_row(
                raw=raw,
                line_no=cast(int, line_no),
                column_map=column_map,
                quarter=quarter,
                noc_version=layout.noc_version,
                source_file=path.name,
                tally=tally,
            )
            if outcome.values is not None:
                rows.append(outcome.values)
            else:
                rejects.append(
                    IngestReject(
                        ingest_run_id=run.id,
                        source_file=path.name,
                        line_no=cast(int, line_no),
                        reason=outcome.reject_reason or "unknown",
                        detail=outcome.reject_detail,
                        raw_row={str(k): clean(v) for k, v in raw.items()},
                    )
                )

        # A reload replaces the file's previous rejects too, or they accumulate.
        session.execute(
            delete(IngestReject).where(
                IngestReject.source_file == path.name,
                IngestReject.ingest_run_id != run.id,
            )
        )
        session.add_all(rejects)

        run.rows_loaded = _upsert(session, rows, run.id)
        run.rows_rejected = len(rejects)
        run.rows_read = run.rows_loaded + run.rows_rejected

        # Anything still carrying an older run id came from a line that no
        # longer exists in the file. This is what makes a reload of a *shorter*
        # file idempotent rather than merely additive.
        stale = cast(
            CursorResult,
            session.execute(
                delete(EmployerLMIA).where(
                    EmployerLMIA.source_file == path.name,
                    EmployerLMIA.ingest_run_id != run.id,
                )
            ),
        )
        if stale.rowcount:
            tally.note("stale_rows_removed")
            log.info("%s: removed %d row(s) no longer present", path.name, stale.rowcount)

        # Cross-check the formats the publisher shipped against each other.
        for sibling in src.siblings:
            try:
                sibling_frame, _ = discover.read_raw(
                    sibling, discover.FORMATS[sibling.suffix.lower()]
                )
                sibling_rows = len(discover.detect_layout(sibling_frame).data)
            except Exception as exc:  # noqa: BLE001 — a bad sibling must not fail the load
                tally.note("sibling_unreadable", f"{sibling.name}: {type(exc).__name__}")
                continue
            if sibling_rows != run.rows_read:
                tally.note(
                    "sibling_row_count_mismatch",
                    f"{sibling.name}={sibling_rows} vs {path.name}={run.rows_read}",
                )

        run.anomalies = tally.as_dict()
        run.status = "ok"

    except Exception as exc:  # noqa: BLE001 — recorded on the run, then re-raised
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = datetime.now(UTC)
        session.commit()
        raise

    run.finished_at = datetime.now(UTC)
    session.commit()
    return run


def resolve_entities(session: Session) -> int:
    """Group raw employer spellings into entities and link every row to one.

    Done in SQL over the whole table rather than per file, because an employer
    appears across quarters and the grouping is only correct when it sees all
    of them. `mode()` picks the most frequently published spelling as the
    display name, which makes the result stable across reloads instead of
    dependent on row order.

    Everything here is deterministic — exact matching on the normalized key —
    so no two distinct companies can be merged. A pg_trgm similarity pass would
    write rows with `match_method = 'fuzzy'` and is deliberately not done yet.
    """
    session.execute(
        text("""
        INSERT INTO employer_entities (
            employer_key, canonical_name, match_method,
            variant_count, row_count, first_seen_quarter, last_seen_quarter
        )
        SELECT
            employer_key,
            mode() WITHIN GROUP (ORDER BY employer_raw),
            'deterministic',
            COUNT(DISTINCT employer_raw),
            COUNT(*),
            MIN(quarter),
            MAX(quarter)
        FROM employers_lmia
        WHERE employer_key IS NOT NULL
        GROUP BY employer_key
        ON CONFLICT (employer_key) DO UPDATE SET
            canonical_name     = EXCLUDED.canonical_name,
            variant_count      = EXCLUDED.variant_count,
            row_count          = EXCLUDED.row_count,
            first_seen_quarter = EXCLUDED.first_seen_quarter,
            last_seen_quarter  = EXCLUDED.last_seen_quarter
    """)
    )

    linked = cast(
        CursorResult,
        session.execute(
            text("""
        UPDATE employers_lmia AS r
           SET employer_id = e.id
          FROM employer_entities AS e
         WHERE e.employer_key = r.employer_key
           AND r.employer_id IS DISTINCT FROM e.id
    """)
        ),
    )

    # An entity nothing points at any more is left over from a previous load.
    session.execute(
        delete(EmployerEntity).where(
            ~select(EmployerLMIA.id).where(EmployerLMIA.employer_id == EmployerEntity.id).exists()
        )
    )
    session.commit()
    return linked.rowcount or 0


def load_all(session: Session, raw_dir: Path) -> list[IngestRun]:
    """Load every discoverable file, then rebuild the employer entities."""
    sources = discover.discover(raw_dir)
    if not sources:
        log.warning("no source files found under %s", raw_dir)
        return []

    runs: list[IngestRun] = []
    for src in sources:
        log.info("loading %s (%s)", src.name, src.quarter or "quarter from banner")
        runs.append(load_file(session, src))

    resolve_entities(session)
    return runs


def summary(session: Session) -> dict[str, Any]:
    """Counts for the ingest report."""
    rows = session.scalar(select(func.count()).select_from(EmployerLMIA)) or 0
    entities = session.scalar(select(func.count()).select_from(EmployerEntity)) or 0
    variants = session.scalar(select(func.count(func.distinct(EmployerLMIA.employer_raw)))) or 0
    rejects = session.scalar(select(func.count()).select_from(IngestReject)) or 0
    quarters = session.scalars(
        select(EmployerLMIA.quarter).distinct().order_by(EmployerLMIA.quarter)
    )
    return {
        "rows": rows,
        "raw_employer_strings": variants,
        "resolved_entities": entities,
        "rejects": rejects,
        "quarters": list(quarters),
    }
