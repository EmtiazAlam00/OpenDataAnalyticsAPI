"""Derived statistics.

These are the endpoints the raw files cannot answer, and they are also the ones
easiest to read wrongly. Two choices here are deliberate:

* `group_by` is explicit rather than implied. What counts as "one employer"
  is a judgement — legal entity, brand, or published string — and the caller
  should be the one making it.
* Every response reports the rows it could not attribute, instead of quietly
  dropping them from the denominator.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, case, cast, func, null, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import EmployerEntity, EmployerLMIA
from app.schemas.stats import GroupBy, Metric, OccupationStat, ProvinceStat, TopEmployer
from ingest.normalize.province import NAMES as PROVINCE_NAMES

router = APIRouter(prefix="/stats", tags=["stats"])

_POSITIONS = func.coalesce(func.sum(EmployerLMIA.approved_positions), 0)
_LMIAS = func.coalesce(func.sum(EmployerLMIA.approved_lmias), 0)


def _filtered(quarter: str | None, province: str | None, exclude_pr_only: bool):
    statement = select(EmployerLMIA)
    if quarter:
        statement = statement.where(EmployerLMIA.quarter == quarter)
    if province:
        statement = statement.where(EmployerLMIA.province == province.upper())
    if exclude_pr_only:
        statement = statement.where(EmployerLMIA.is_pr_only.is_(False))
    return statement.whereclause


@router.get("/top-employers", response_model=list[TopEmployer])
def top_employers(
    session: Session = Depends(get_session),
    quarter: str | None = Query(None, pattern=r"^\d{4}Q[1-4]$"),
    province: str | None = Query(None, min_length=2, max_length=2),
    group_by: GroupBy = Query(GroupBy.entity, description="What counts as one employer"),
    metric: Metric = Query(Metric.positions, description="Rank by positions or by LMIA count"),
    exclude_pr_only: bool = Query(False, description="Drop 'Permanent Resident Only' LMIAs"),
    limit: int = Query(20, ge=1, le=200),
) -> list[TopEmployer]:
    """Employers with the most approved positions.

    `group_by=raw` reproduces what a naive `GROUP BY employer` over the source
    files would give you. Comparing it against `group_by=entity` shows exactly
    what the name normalization changed — for the 2025–26 quarters the top of
    the ranking is unaffected, because the largest employers are single-name
    agricultural operations, while mid-field names with many spellings move
    substantially.
    """
    where = _filtered(quarter, province, exclude_pr_only)

    # The grouping expression differs per branch: a mapped column, a COALESCE,
    # or a plain column. They share no common SQLAlchemy type.
    label: Any
    group_cols: list[Any]

    if group_by is GroupBy.entity:
        label, group_cols = (
            EmployerEntity.canonical_name,
            [
                EmployerEntity.id,
                EmployerEntity.canonical_name,
                EmployerEntity.variant_count,
            ],
        )
        statement = select(
            EmployerEntity.id,
            label,
            _POSITIONS,
            _LMIAS,
            func.count(),
            EmployerEntity.variant_count,
        ).join(EmployerEntity, EmployerLMIA.entity)
    elif group_by is GroupBy.brand:
        label = func.coalesce(EmployerLMIA.brand_key, EmployerLMIA.employer_key)
        group_cols = [label]
        statement = select(
            # Labelled because two unlabelled CAST(NULL) columns in one select
            # are indistinguishable when the result row is resolved.
            cast(null(), Integer).label("entity_id"),
            label,
            _POSITIONS,
            _LMIAS,
            func.count(),
            func.count(func.distinct(EmployerLMIA.employer_raw)).label("variant_count"),
        )
    else:
        label = EmployerLMIA.employer_raw
        group_cols = [label]
        statement = select(
            cast(null(), Integer).label("entity_id"),
            label,
            _POSITIONS,
            _LMIAS,
            func.count(),
            cast(null(), Integer).label("variant_count"),
        )

    if where is not None:
        statement = statement.where(where)

    order = _POSITIONS if metric is Metric.positions else _LMIAS
    rows = session.execute(
        statement.group_by(*group_cols).order_by(order.desc(), label).limit(limit)
    ).all()

    return [
        TopEmployer(
            rank=i,
            entity_id=r[0],
            name=r[1],
            approved_positions=r[2],
            approved_lmias=r[3],
            rows=r[4],
            variant_count=r[5],
        )
        for i, r in enumerate(rows, 1)
    ]


@router.get("/by-province", response_model=list[ProvinceStat])
def by_province(
    session: Session = Depends(get_session),
    quarter: str | None = Query(None, pattern=r"^\d{4}Q[1-4]$"),
    exclude_pr_only: bool = Query(False),
    include_non_provinces: bool = Query(
        True,
        description=(
            "Include the 'head office outside Canada' bucket the source files put "
            "in the province column. It is not a region; it is reported separately "
            "rather than silently folded into a province."
        ),
    ),
) -> list[ProvinceStat]:
    statement = select(
        EmployerLMIA.province,
        EmployerLMIA.location_scope,
        func.count(func.distinct(EmployerLMIA.employer_id)),
        func.count(),
        _POSITIONS,
        _LMIAS,
    ).group_by(EmployerLMIA.province, EmployerLMIA.location_scope)

    where = _filtered(quarter, None, exclude_pr_only)
    if where is not None:
        statement = statement.where(where)
    if not include_non_provinces:
        statement = statement.where(EmployerLMIA.province.is_not(None))

    rows = session.execute(statement.order_by(_POSITIONS.desc())).all()

    return [
        ProvinceStat(
            province=r[0],
            province_name=(
                PROVINCE_NAMES.get(r[0], r[0])
                if r[0]
                else {
                    "outside_canada": "Head office outside Canada",
                    "unknown": "Unrecognised location",
                }.get(r[1], "Unrecognised location")
            ),
            employers=r[2],
            rows=r[3],
            approved_positions=r[4],
            approved_lmias=r[5],
        )
        for r in rows
    ]


@router.get("/by-occupation", response_model=list[OccupationStat])
def by_occupation(
    session: Session = Depends(get_session),
    quarter: str | None = Query(None, pattern=r"^\d{4}Q[1-4]$"),
    province: str | None = Query(None, min_length=2, max_length=2),
    exclude_pr_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=200),
) -> list[OccupationStat]:
    """Occupations with the most approved positions.

    NOC codes are only comparable within one classification vintage, so
    `noc_version` is returned on every row rather than left for the caller to
    infer. Mixing 2011 and 2021 codes in one ranking would be meaningless; the
    version is grouped on to make any such mixture visible.
    """
    statement = select(
        EmployerLMIA.noc_code,
        func.min(EmployerLMIA.noc_title),
        EmployerLMIA.noc_version,
        func.count(func.distinct(EmployerLMIA.employer_id)),
        _POSITIONS,
        _LMIAS,
    ).group_by(EmployerLMIA.noc_code, EmployerLMIA.noc_version)

    where = _filtered(quarter, province, exclude_pr_only)
    if where is not None:
        statement = statement.where(where)

    rows = session.execute(statement.order_by(_POSITIONS.desc()).limit(limit)).all()

    return [
        OccupationStat(
            rank=i,
            noc_code=r[0],
            noc_title=r[1],
            noc_version=r[2],
            employers=r[3],
            approved_positions=r[4],
            approved_lmias=r[5],
        )
        for i, r in enumerate(rows, 1)
    ]


@router.get("/by-stream", response_model=list[dict])
def by_stream(
    session: Session = Depends(get_session),
    quarter: str | None = Query(None, pattern=r"^\d{4}Q[1-4]$"),
) -> list[dict]:
    """Positions by program stream, including rows whose stream did not map."""
    statement = select(
        func.coalesce(EmployerLMIA.program_stream, "(unmapped)"),
        func.count(),
        _POSITIONS,
        _LMIAS,
        func.sum(case((EmployerLMIA.program_stream.is_(None), 1), else_=0)),
    ).group_by(func.coalesce(EmployerLMIA.program_stream, "(unmapped)"))

    if quarter:
        statement = statement.where(EmployerLMIA.quarter == quarter)

    rows = session.execute(statement.order_by(_POSITIONS.desc())).all()
    return [
        {
            "program_stream": r[0],
            "rows": r[1],
            "approved_positions": r[2],
            "approved_lmias": r[3],
            "unmapped_rows": r[4],
        }
        for r in rows
    ]
