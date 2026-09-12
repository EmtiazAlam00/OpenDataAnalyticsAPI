"""Address parsing.

The column is a single string shaped 'City, PROV A1B 2C3'. Two things about it
are worth knowing:

1. 1,463 of 45,078 rows in the 2025–26 files carry a *truncated* postal code —
   'St-François, NB E7A  1A' rather than 'E7A 1A1'. The final character is
   missing and the gap is padded with a second space. This is a defect in the
   publisher's export, concentrated in Quebec and New Brunswick. The forward
   sortation area (the first three characters) survives it, and the FSA is the
   useful geographic unit anyway, so it is extracted separately.

2. The province abbreviation embedded here is a second, independent witness to
   the Province/Territory column. The loader compares them and counts
   disagreements rather than silently trusting either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ingest.normalize.text import clean, squash

# A full Canadian postal code, tolerating the doubled space the export leaves
# behind. FSA is the leading 'A1B'; LDU the trailing '2C3'.
_POSTAL = re.compile(r"\b(?P<fsa>[A-Z]\d[A-Z])\s*(?P<ldu>\d[A-Z]\d)\b", re.IGNORECASE)
_FSA_ONLY = re.compile(r"\b(?P<fsa>[A-Z]\d[A-Z])\b(?!\s*\d[A-Z]\d)", re.IGNORECASE)
_PROV_CODE = re.compile(r",\s*(?P<code>[A-Z]{2})\b(?=[\s,]|$)")


@dataclass(frozen=True)
class Address:
    raw: str | None
    city: str | None
    province_code: str | None
    postal_code: str | None
    postal_fsa: str | None
    postal_truncated: bool


EMPTY = Address(
    raw=None,
    city=None,
    province_code=None,
    postal_code=None,
    postal_fsa=None,
    postal_truncated=False,
)


def parse(value: object) -> Address:
    raw = clean(value)
    if raw is None:
        return EMPTY

    postal_code: str | None = None
    postal_fsa: str | None = None
    truncated = False

    if m := _POSTAL.search(raw):
        postal_fsa = m.group("fsa").upper()
        postal_code = f"{postal_fsa} {m.group('ldu').upper()}"
    elif m := _FSA_ONLY.search(raw):
        postal_fsa = m.group("fsa").upper()
        truncated = True

    province_code = m.group("code").upper() if (m := _PROV_CODE.search(raw)) else None

    # Everything before the last comma is the locality. Splitting from the
    # right keeps hyphenated and multi-word place names intact —
    # 'Grand Falls-Windsor, NL A2A 1X3' and 'St. John's, NL A1C 6C9' both work.
    city = squash(raw.rsplit(",", 1)[0]) if "," in raw else None

    return Address(
        raw=raw,
        city=city or None,
        province_code=province_code,
        postal_code=postal_code,
        postal_fsa=postal_fsa,
        postal_truncated=truncated,
    )
