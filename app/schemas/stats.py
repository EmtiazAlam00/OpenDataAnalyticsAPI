from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class GroupBy(StrEnum):
    """How to decide what counts as 'one employer'.

    `entity` is the legal employer the LMIA was issued to, with spelling
    variants merged. `brand` groups franchisees under the name they trade as,
    so the thirteen companies operating Tim Hortons outlets count once. `raw`
    groups the published strings untouched — the honest baseline, and the one
    that shows what the normalization actually changed.
    """

    entity = "entity"
    brand = "brand"
    raw = "raw"


class Metric(StrEnum):
    positions = "positions"
    lmias = "lmias"


class TopEmployer(BaseModel):
    rank: int
    name: str
    entity_id: int | None = Field(default=None, description="Null when grouping by brand or raw")
    approved_positions: int
    approved_lmias: int
    rows: int
    variant_count: int | None = Field(
        default=None, description="Published spellings merged into this group"
    )


class ProvinceStat(BaseModel):
    province: str | None = Field(description="Two-letter code; null for the non-province bucket")
    province_name: str
    employers: int = Field(description="Distinct resolved legal entities")
    rows: int
    approved_positions: int
    approved_lmias: int


class OccupationStat(BaseModel):
    rank: int
    noc_code: str | None
    noc_title: str | None
    noc_version: str | None
    employers: int
    approved_positions: int
    approved_lmias: int


class SeriesBreak(BaseModel):
    """A point where the series stops being comparable with itself."""

    quarter: str
    reason: str
    detail: str


class TrendPoint(BaseModel):
    quarter: str
    approved_positions: int
    approved_lmias: int
    employers: int
    rows: int
    pr_only_positions: int = Field(
        description="Of the above, positions from 'Permanent Resident Only' LMIAs"
    )


class TrendSeries(BaseModel):
    """A time series, plus the reasons not to read it naively.

    `series_breaks` is not decoration. The published list changed what it
    counted at 2023Q4, and a chart drawn straight through that point shows a
    rise that is partly an artefact of the definition changing. Clients that
    ignore this field will draw that chart; clients that read it can at least
    annotate it. `exclude_pr_only=true` removes the discontinuity at source.
    """

    series: list[TrendPoint]
    series_breaks: list[SeriesBreak]
    excluded_pr_only: bool
    comparable: bool = Field(
        description="False when the range spans a break and PR-only rows were not excluded"
    )
