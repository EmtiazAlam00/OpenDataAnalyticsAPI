from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import EmployerLMIA, IngestRun

router = APIRouter(tags=["meta"])


@router.get("/quarters", summary="Quarters currently loaded")
def list_quarters(session: Session = Depends(get_session)) -> list[dict]:
    """What is actually in the database, and what each quarter is comparable with.

    `noc_version` and `includes_pr_only` are reported per quarter because they
    determine whether two quarters can be compared at all — not as trivia.
    """
    rows = session.execute(
        select(
            EmployerLMIA.quarter,
            func.count(),
            func.coalesce(func.sum(EmployerLMIA.approved_positions), 0),
            func.coalesce(func.sum(EmployerLMIA.approved_lmias), 0),
            func.count(func.distinct(EmployerLMIA.employer_id)),
            func.min(EmployerLMIA.noc_version),
            func.max(EmployerLMIA.noc_version),
            func.bool_or(EmployerLMIA.is_pr_only),
        )
        .group_by(EmployerLMIA.quarter)
        .order_by(EmployerLMIA.quarter)
    ).all()

    sources = {
        r[0]: r[1]
        for r in session.execute(
            select(IngestRun.quarter, func.max(IngestRun.source_file))
            .where(IngestRun.status == "ok")
            .group_by(IngestRun.quarter)
        ).all()
    }

    return [
        {
            "quarter": r[0],
            "rows": r[1],
            "approved_positions": r[2],
            "approved_lmias": r[3],
            "employers": r[4],
            "noc_version": r[5] if r[5] == r[6] else f"{r[5]}+{r[6]}",
            "includes_pr_only": r[7],
            "source_file": sources.get(r[0]),
        }
        for r in rows
    ]


@router.get("/meta/ingest", summary="What was loaded, and what was not")
def ingest_report(session: Session = Depends(get_session)) -> dict:
    """The loader's own audit trail.

    Surfaced as an endpoint because a pipeline that reports only its successes
    is not reporting anything. `anomalies` counts values that failed to
    normalize but did not cost the row; `rows_rejected` counts rows that could
    not be loaded at all.
    """
    runs = session.scalars(
        select(IngestRun).order_by(IngestRun.quarter, IngestRun.started_at.desc())
    ).all()

    latest: dict[str, IngestRun] = {}
    for run in runs:
        latest.setdefault(run.source_file, run)

    return {
        "files": [
            {
                "source_file": run.source_file,
                "quarter": run.quarter,
                "format": run.file_format,
                "sha256": run.file_sha256[:16],
                "status": run.status,
                "rows_read": run.rows_read,
                "rows_loaded": run.rows_loaded,
                "rows_rejected": run.rows_rejected,
                "balances": run.rows_read == run.rows_loaded + run.rows_rejected,
                "noc_version": run.noc_version,
                "anomalies": run.anomalies or {},
                "unmapped_columns": run.unmapped_columns or [],
                "error": run.error,
                "loaded_at": run.finished_at,
            }
            for run in sorted(latest.values(), key=lambda r: (r.quarter or "", r.source_file))
        ],
        "publisher_notes": {
            run.quarter: run.notes for run in sorted(latest.values(), key=lambda r: r.quarter or "")
        },
        "total_attempts": len(runs),
    }
