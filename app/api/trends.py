"""Quarter-over-quarter trends.

The reason this is its own module rather than one more `/stats` endpoint: a
time series drawn from this dataset is misleading by default, and the endpoint
has to do something about that rather than leave a warning in the
documentation.

From 2018Q1 to 2023Q3 the published list excluded LMIAs supporting Permanent
Residence. From 2023Q4 it includes them, and the earlier lists were never
revised. So a series that spans that boundary shows a step change that is
partly a change in what is being counted. Plotting it without saying so
produces a chart that is wrong in a way no one can see.

Two mitigations, both returned in the response: `series_breaks` names the
discontinuity, and `exclude_pr_only=true` removes it at source by dropping the
rows that were added to the definition.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import EmployerLMIA
from app.schemas.stats import SeriesBreak, TrendPoint, TrendSeries

router = APIRouter(prefix="/trends", tags=["trends"])

PR_ONLY_BREAK = SeriesBreak(
    quarter=settings.pr_only_from_quarter,
    reason="inclusion_criteria_changed",
    detail=(
        "From this quarter onward the published list includes LMIAs supporting "
        "Permanent Residence ('PR Only'), which earlier lists excluded. Earlier "
        "quarters were not retroactively revised, so part of any increase across "
        "this point reflects the change in what is counted rather than a change "
        "in activity. Pass exclude_pr_only=true for a like-for-like series."
    ),
)


@router.get("/positions", response_model=TrendSeries)
def positions_over_time(
    session: Session = Depends(get_session),
    from_quarter: str | None = Query(None, alias="from", pattern=r"^\d{4}Q[1-4]$"),
    to_quarter: str | None = Query(None, alias="to", pattern=r"^\d{4}Q[1-4]$"),
    province: str | None = Query(None, min_length=2, max_length=2),
    stream: str | None = None,
    noc: str | None = None,
    exclude_pr_only: bool = Query(
        False, description="Drop PR-only LMIAs so the series is comparable across 2023Q4"
    ),
) -> TrendSeries:
    if from_quarter and to_quarter and from_quarter > to_quarter:
        raise HTTPException(status_code=422, detail="'from' is later than 'to'")

    statement = select(
        EmployerLMIA.quarter,
        func.coalesce(func.sum(EmployerLMIA.approved_positions), 0),
        func.coalesce(func.sum(EmployerLMIA.approved_lmias), 0),
        func.count(func.distinct(EmployerLMIA.employer_id)),
        func.count(),
        # The PR-only share of each point, so a caller can see the size of the
        # discontinuity rather than only being told it exists.
        func.coalesce(
            func.sum(
                case(
                    (EmployerLMIA.is_pr_only, func.coalesce(EmployerLMIA.approved_positions, 0)),
                    else_=0,
                )
            ),
            0,
        ).cast(Integer),
    ).group_by(EmployerLMIA.quarter)

    # 'YYYYQn' compares correctly as text: the year dominates and the quarter
    # digit is single, so lexical and chronological order coincide.
    if from_quarter:
        statement = statement.where(EmployerLMIA.quarter >= from_quarter)
    if to_quarter:
        statement = statement.where(EmployerLMIA.quarter <= to_quarter)
    if province:
        statement = statement.where(EmployerLMIA.province == province.upper())
    if stream:
        statement = statement.where(EmployerLMIA.program_stream == stream)
    if noc:
        statement = statement.where(EmployerLMIA.noc_code == noc)
    if exclude_pr_only:
        statement = statement.where(EmployerLMIA.is_pr_only.is_(False))

    rows = session.execute(statement.order_by(EmployerLMIA.quarter)).all()

    series = [
        TrendPoint(
            quarter=r[0],
            approved_positions=r[1],
            approved_lmias=r[2],
            employers=r[3],
            rows=r[4],
            pr_only_positions=r[5],
        )
        for r in rows
    ]

    # A break is only reported when the requested range actually spans it —
    # a series entirely after 2023Q4 is internally consistent.
    breaks: list[SeriesBreak] = []
    if series:
        first, last = series[0].quarter, series[-1].quarter
        if first < settings.pr_only_from_quarter <= last:
            breaks.append(PR_ONLY_BREAK)

    return TrendSeries(
        series=series,
        series_breaks=breaks,
        excluded_pr_only=exclude_pr_only,
        comparable=not breaks or exclude_pr_only,
    )
