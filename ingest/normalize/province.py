"""Province/Territory normalization.

The source pads these to a fixed width, so 'Ontario' arrives as at least three
distinct strings. It also uses the column for a value that is not a province at
all — employers headquartered outside Canada — which must not be allowed to
appear in a per-province breakdown as though it were a fourteenth region.
"""

from __future__ import annotations

from ingest.normalize.text import clean, strip_accents

# Scope of a row's location, kept separate from the province code so that
# /stats/by-province can exclude non-province buckets without special-casing
# a magic string at query time.
SCOPE_PROVINCE = "province"
SCOPE_OUTSIDE_CANADA = "outside_canada"
SCOPE_UNKNOWN = "unknown"

_CANONICAL = {
    "newfoundland and labrador": "NL",
    "prince edward island": "PE",
    "nova scotia": "NS",
    "new brunswick": "NB",
    "quebec": "QC",
    "ontario": "ON",
    "manitoba": "MB",
    "saskatchewan": "SK",
    "alberta": "AB",
    "british columbia": "BC",
    "yukon": "YT",
    "northwest territories": "NT",
    "nunavut": "NU",
}

# French forms, and the abbreviations older files may use directly. Accents are
# stripped before lookup, so these keys are written unaccented.
_ALIASES = {
    "terre-neuve-et-labrador": "NL",
    "ile-du-prince-edouard": "PE",
    "nouvelle-ecosse": "NS",
    "nouveau-brunswick": "NB",
    "colombie-britannique": "BC",
    "territoires du nord-ouest": "NT",
    "yukon territory": "YT",
    "newfoundland": "NL",
    "quebec (province)": "QC",
    "nwt": "NT",
    **{code.lower(): code for code in _CANONICAL.values()},
}

_OUTSIDE_CANADA_MARKERS = ("head office outside", "outside of canada", "outside canada")

NAMES = {code: name.title() for name, code in _CANONICAL.items()}
NAMES["NL"] = "Newfoundland and Labrador"
NAMES["PE"] = "Prince Edward Island"

CODES = tuple(_CANONICAL.values())


def normalize(value: object) -> tuple[str | None, str]:
    """Return (two-letter code, scope).

    The code is None for anything that is not one of the thirteen provinces and
    territories; `scope` says why, so an unmapped value is distinguishable from
    a deliberately non-province bucket.
    """
    raw = clean(value)
    if raw is None:
        return None, SCOPE_UNKNOWN

    folded = strip_accents(raw).lower().strip(" .")

    if any(marker in folded for marker in _OUTSIDE_CANADA_MARKERS):
        return None, SCOPE_OUTSIDE_CANADA

    if code := _CANONICAL.get(folded) or _ALIASES.get(folded):
        return code, SCOPE_PROVINCE

    return None, SCOPE_UNKNOWN
