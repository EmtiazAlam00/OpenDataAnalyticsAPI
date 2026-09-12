"""The dashboard.

Served by the API rather than built as a separate frontend, for one practical
reason: the page reads from this API over same-origin `fetch`, so it needs no
CORS configuration, no build step, and no second process to run. It is one
static HTML file with no dependencies — opening `/dash` is the whole setup.

It is deliberately a *reader* of the public endpoints. Everything it shows comes
from `/quarters`, `/stats/*`, `/trends/positions`, `/meta/ingest` and `/about`,
so the dashboard cannot show a number the API would not also give you.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["dashboard"])

DASH_HTML = Path(__file__).resolve().parent.parent / "static" / "dash.html"


@router.get("/dash", include_in_schema=False)
def dashboard() -> FileResponse:
    if not DASH_HTML.is_file():
        raise HTTPException(status_code=500, detail=f"dashboard asset missing: {DASH_HTML}")
    # no-cache so an edit to the file shows up on reload rather than after a
    # browser cache clear; the file is small and served locally.
    return FileResponse(DASH_HTML, media_type="text/html", headers={"Cache-Control": "no-store"})
