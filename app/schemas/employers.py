from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EmployerRow(BaseModel):
    """One published row: an employer, an occupation, a location, a quarter."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    quarter: str

    employer: str = Field(validation_alias="employer_raw", description="Name exactly as published")
    employer_id: int | None = Field(
        default=None, description="Resolved legal-entity id; see /entities/{id}"
    )
    trade_name: str | None = Field(
        default=None,
        validation_alias="trade_name_raw",
        description="Brand operated under, where the name declares one ('… o/a Tim Hortons')",
    )

    province: str | None = Field(
        default=None, description="Two-letter code; null if not a province"
    )
    location_scope: str = Field(
        description="'province', 'outside_canada' for head offices abroad, or 'unknown'"
    )
    city: str | None = None
    postal_code: str | None = None
    address: str | None = Field(default=None, validation_alias="address_raw")

    program_stream: str | None = Field(
        default=None, description="Canonical stream, null if unmapped"
    )
    is_pr_only: bool = Field(description="Row comes from the PR-only inclusion added at 2023Q4")
    incorporate_status: str | None = None

    noc_code: str | None = None
    noc_title: str | None = None
    noc_version: str | None = Field(default=None, description="'2011' or '2021'")

    approved_lmias: int | None = None
    approved_positions: int | None = Field(
        default=None, description="Positions authorised, not workers who arrived"
    )

    source_file: str


class EntitySummary(BaseModel):
    """A resolved legal employer: one or more published spellings of one company."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    canonical_name: str
    employer_key: str
    match_method: str = Field(description="How the grouping was made: 'deterministic'")
    variant_count: int = Field(
        description="Distinct published spellings collapsed into this entity"
    )
    row_count: int
    first_seen_quarter: str | None = None
    last_seen_quarter: str | None = None


class EntityDetail(EntitySummary):
    """An entity plus the evidence for it.

    `variants` is the audit trail: every raw string that landed on this entity.
    Any grouping this API performs has to be inspectable, or it is just an
    unfalsifiable claim about the data.
    """

    variants: list[str]
    total_approved_positions: int
    total_approved_lmias: int
    quarters: list[str]
