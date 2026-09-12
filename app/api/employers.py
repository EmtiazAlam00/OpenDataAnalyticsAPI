from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import EmployerEntity, EmployerLMIA
from app.schemas.common import Page
from app.schemas.employers import EmployerRow, EntityDetail, EntitySummary

router = APIRouter(tags=["employers"])


@router.get("/employers", response_model=Page[EmployerRow], summary="Search and filter rows")
def list_employers(
    session: Session = Depends(get_session),
    q: str | None = Query(None, description="Substring match on the published employer name"),
    province: str | None = Query(None, min_length=2, max_length=2, description="e.g. ON"),
    stream: str | None = Query(None, description="Canonical program stream"),
    noc: str | None = Query(None, description="NOC code, exact"),
    quarter: str | None = Query(None, pattern=r"^\d{4}Q[1-4]$"),
    incorporate_status: str | None = None,
    pr_only: bool | None = Query(None, description="Restrict to, or exclude, PR-only LMIAs"),
    min_positions: int | None = Query(None, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
) -> Page[EmployerRow]:
    statement = select(EmployerLMIA)

    if q:
        # Matches the published spelling and the resolved name, so a search for
        # 'tim hortons' also finds '1317518 Alberta Ltd. o/a Tim Hortons'.
        pattern = f"%{q}%"
        statement = statement.outerjoin(EmployerEntity, EmployerLMIA.entity).where(
            or_(
                EmployerLMIA.employer_raw.ilike(pattern),
                EmployerLMIA.trade_name_raw.ilike(pattern),
                EmployerEntity.canonical_name.ilike(pattern),
            )
        )
    if province:
        statement = statement.where(EmployerLMIA.province == province.upper())
    if stream:
        statement = statement.where(EmployerLMIA.program_stream == stream)
    if noc:
        statement = statement.where(EmployerLMIA.noc_code == noc)
    if quarter:
        statement = statement.where(EmployerLMIA.quarter == quarter)
    if incorporate_status:
        statement = statement.where(EmployerLMIA.incorporate_status == incorporate_status)
    if pr_only is not None:
        statement = statement.where(EmployerLMIA.is_pr_only.is_(pr_only))
    if min_positions is not None:
        statement = statement.where(EmployerLMIA.approved_positions >= min_positions)

    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0

    rows = session.scalars(
        statement.order_by(EmployerLMIA.approved_positions.desc().nullslast(), EmployerLMIA.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return Page.build(
        [EmployerRow.model_validate(r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.get("/employers/{row_id}", response_model=EmployerRow, summary="One published row")
def get_employer_row(
    row_id: int = Path(ge=1), session: Session = Depends(get_session)
) -> EmployerRow:
    row = session.get(EmployerLMIA, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No row with id {row_id}")
    return EmployerRow.model_validate(row)


@router.get("/entities", response_model=Page[EntitySummary], tags=["entities"])
def list_entities(
    session: Session = Depends(get_session),
    q: str | None = Query(None, description="Substring match on the resolved name"),
    min_variants: int = Query(1, ge=1, description="Use 2+ to see only names that were merged"),
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
) -> Page[EntitySummary]:
    """Resolved legal employers — the result of collapsing spelling variants."""
    statement = select(EmployerEntity).where(EmployerEntity.variant_count >= min_variants)
    if q:
        statement = statement.where(EmployerEntity.canonical_name.ilike(f"%{q}%"))

    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(
        statement.order_by(EmployerEntity.row_count.desc(), EmployerEntity.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return Page.build(
        [EntitySummary.model_validate(r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.get("/entities/{entity_id}", response_model=EntityDetail, tags=["entities"])
def get_entity(
    entity_id: int = Path(ge=1), session: Session = Depends(get_session)
) -> EntityDetail:
    """One resolved employer, with every raw spelling that was merged into it.

    The `variants` list is the point of this endpoint: any grouping the API
    performs should be checkable by the person relying on it.
    """
    entity = session.get(EmployerEntity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"No entity with id {entity_id}")

    totals = session.execute(
        select(
            func.coalesce(func.sum(EmployerLMIA.approved_positions), 0),
            func.coalesce(func.sum(EmployerLMIA.approved_lmias), 0),
        ).where(EmployerLMIA.employer_id == entity_id)
    ).one()

    variants = session.scalars(
        select(EmployerLMIA.employer_raw)
        .where(EmployerLMIA.employer_id == entity_id)
        .distinct()
        .order_by(EmployerLMIA.employer_raw)
    ).all()

    quarters = session.scalars(
        select(EmployerLMIA.quarter)
        .where(EmployerLMIA.employer_id == entity_id)
        .distinct()
        .order_by(EmployerLMIA.quarter)
    ).all()

    return EntityDetail(
        **EntitySummary.model_validate(entity).model_dump(),
        variants=list(variants),
        total_approved_positions=totals[0],
        total_approved_lmias=totals[1],
        quarters=list(quarters),
    )
