from fastapi import FastAPI

from app.api import dash, employers, meta, quarters, stats, trends

DESCRIPTION = """
A queryable API over the Government of Canada **Temporary Foreign Worker Program
(TFWP) — Positive Labour Market Impact Assessment (LMIA) Employers List**.

The source is published as quarterly record-level files whose column names, NOC
classification and inclusion criteria all shift over the period they cover. This
service unifies them into one schema and serves search plus derived analytics on
top.

**Read `/about` before drawing conclusions from these numbers.** The list is
incomplete by construction, it counts approved positions rather than workers,
and it contains a structural break at 2023Q4.

A dashboard over these same endpoints is at [`/dash`](/dash).

> Contains information licensed under the Open Government Licence – Canada.
"""

app = FastAPI(
    title="TFWP Data API",
    version="0.1.0",
    description=DESCRIPTION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(dash.router)
app.include_router(meta.router)
app.include_router(quarters.router)
app.include_router(employers.router)
app.include_router(stats.router)
app.include_router(trends.router)
