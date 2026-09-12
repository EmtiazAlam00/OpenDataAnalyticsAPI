"""Integer parsing for the count columns.

`Approved LMIAs` and `Approved Positions` are clean integers in the 2025–26
files — zero nulls, zero unparseable values. This is deliberately defensive
anyway: the count columns are the ones every derived statistic sums, so a
silently coerced value here is a wrong number on an endpoint rather than a
visible failure. Anything that does not parse is preserved in the companion
`*_raw` column and counted as an anomaly on the ingest run.
"""

from __future__ import annotations

import re

from ingest.normalize.text import clean

# Thousands separators, and the footnote markers government tables attach to
# suppressed or revised figures.
_STRIP = re.compile(r"[,\s ]|\*+$|†+$")
_INTEGER = re.compile(r"^-?\d+$")
_RANGE = re.compile(r"^\d+\s*(?:-|to|–)\s*\d+$", re.IGNORECASE)


def parse_int(value: object) -> tuple[int | None, str | None, str | None]:
    """Return (parsed, raw, reason-if-unparsed).

    A range such as '1-5' is explicitly *not* collapsed to either endpoint.
    Picking one would invent precision the publisher did not provide, so the
    row keeps its raw text and contributes nothing to any sum.
    """
    raw = clean(value)
    if raw is None:
        return None, None, None

    candidate = _STRIP.sub("", raw)
    if _INTEGER.match(candidate):
        parsed = int(candidate)
        if parsed < 0:
            return None, raw, "negative"
        return parsed, raw, None

    if _RANGE.match(candidate):
        return None, raw, "range"

    return None, raw, "unparseable"
