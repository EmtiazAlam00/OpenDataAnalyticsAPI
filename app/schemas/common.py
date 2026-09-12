from __future__ import annotations

from pydantic import BaseModel, Field


class Page[T](BaseModel):
    items: list[T]
    total: int = Field(description="Total matching rows, ignoring pagination")
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> Page[T]:
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=max(1, -(-total // page_size)),
        )


class Caveat(BaseModel):
    """A reason a number on this API means less than it appears to."""

    id: str
    summary: str
    detail: str
