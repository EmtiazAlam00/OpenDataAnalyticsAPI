"""Occupation splitting.

The source packs the NOC code and title into one cell: '13100-Administrative
officers'. Across the four 2025–26 quarters this is the cleanest column in the
dataset — 45,078 of 45,078 rows match the expected shape — but the split still
has to be careful, because titles routinely contain hyphens of their own
('72020-Contractors and supervisors, mechanic trades' is fine, but
'63200-Cooks' and '94142-Fish and seafood plant workers' sit next to entries
like '14100-General office support workers - clerical'). Splitting on the first
hyphen only is what makes that safe.

NOC 2011 codes are 4 digits; NOC 2021 codes are 5. The width is therefore a
signal about which classification a row was published under, but not a reliable
one — see `infer_version`.
"""

from __future__ import annotations

import re

from ingest.normalize.text import clean

NOC_2011 = "2011"
NOC_2021 = "2021"

# Code, then the first hyphen, then everything else. Anchored so a title that
# happens to begin with digits cannot be mistaken for a code.
_PACKED = re.compile(r"^\s*(?P<code>\d{4,5})\s*-\s*(?P<title>.+?)\s*$", re.DOTALL)


def split(value: object) -> tuple[str | None, str | None]:
    """Split 'NNNNN-Title' into (code, title).

    Returns (None, raw) when the cell does not carry a code, so an unexpected
    format keeps the text rather than discarding it.
    """
    raw = clean(value)
    if raw is None:
        return None, None
    if m := _PACKED.match(raw):
        return m.group("code"), m.group("title")
    return None, raw


def infer_version(code: str | None, banner_version: str | None) -> str | None:
    """Decide which NOC classification a row belongs to.

    The banner at the top of each sheet states the vintage explicitly ('…
    National Occupational Classification (NOC) 2021 and Business Location …'),
    and it is authoritative: footnote 4 of the 2025–26 files records that ESDC
    retroactively converted every pre-September-2024 occupation to NOC 2021
    using Statistics Canada's empirical concordance. A file downloaded today
    therefore reports NOC 2021 even for quarters originally published under NOC
    2011, which means the vintage is a property of the *file*, not the quarter.

    Code width is only a fallback for files whose banner does not say.
    """
    if banner_version:
        return banner_version
    if code is None:
        return None
    return NOC_2021 if len(code) == 5 else NOC_2011
