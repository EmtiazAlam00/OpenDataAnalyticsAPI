from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session

router = APIRouter(tags=["meta"])

ATTRIBUTION = "Contains information licensed under the Open Government Licence – Canada."

CAVEATS = [
    {
        "id": "incomplete-by-construction",
        "summary": "The list is not a complete record of positive LMIAs.",
        "detail": (
            "Employers whose business name is a personal name are excluded from "
            "publication. Any count derived from this data is therefore a floor, "
            "not a total."
        ),
    },
    {
        "id": "positions-not-workers",
        "summary": "These are positions on approved LMIAs, not workers.",
        "detail": (
            "A positive LMIA authorises an employer to seek a temporary foreign "
            "worker. It does not mean a work permit was issued, that anyone "
            "entered Canada, or that the position was ever filled. Positions may "
            "also have been cancelled after approval without the list being "
            "updated."
        ),
    },
    {
        "id": "noc-version-drift",
        "summary": "Occupation codes are not comparable across the whole range.",
        "detail": (
            "Earlier quarters classify occupations under NOC 2011; later quarters "
            "use NOC 2021, which restructured both the codes and the hierarchy. "
            "Both are stored exactly as published alongside a noc_version field. "
            "No crosswalk is applied, so filtering by a NOC code will only match "
            "quarters that used that code's classification system."
        ),
    },
    {
        "id": "pr-only-break",
        "summary": f"There is a structural break at {settings.pr_only_from_quarter}.",
        "detail": (
            "From 2018Q1 to 2023Q3, LMIAs supporting an application for Permanent "
            f"Residence were excluded from the published list. From "
            f"{settings.pr_only_from_quarter} onward they are included, and the "
            "earlier lists were not retroactively revised. A rise across that "
            "boundary may reflect the change in what is counted rather than any "
            "change in the underlying activity. Endpoints that return a time "
            "series flag this in a series_breaks field."
        ),
    },
]


@router.get("/health", summary="Liveness and database connectivity")
def health(session: Session = Depends(get_session)) -> dict:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return {"status": "degraded", "database": "unreachable"}
    return {"status": "ok", "database": "ok"}


@router.get("/about", summary="What this dataset is, and what it cannot tell you")
def about() -> dict:
    return {
        "dataset": "Temporary Foreign Worker Program — Positive LMIA Employers List",
        "publisher": "Employment and Social Development Canada",
        "portal": "https://open.canada.ca/en",
        "dataset_id": "90fed587-1364-4f33-a9ee-208181dc0b97",
        "cadence": "quarterly",
        "caveats": CAVEATS,
        "attribution": ATTRIBUTION,
    }


@router.get("/attribution", summary="Open Government Licence acknowledgement")
def attribution() -> dict:
    return {
        "statement": ATTRIBUTION,
        "licence": "Open Government Licence – Canada",
        "licence_url": "https://open.canada.ca/en/open-government-licence-canada",
    }
