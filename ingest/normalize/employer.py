"""Employer name normalization.

Footnote 3 of every quarterly file states the problem outright:

    The employer name is manually entered in the system. As such, accuracy of
    the names is subject to potential data entry error and inconsistent
    spelling.

The consequence is that `GROUP BY employer` ranks *spellings*, not employers.
Across the four 2025–26 quarters there are 31,843 distinct employer strings in
45,078 rows, and a single company routinely appears as several of them:

    TIM HORTONS #4021
    Tim Hortons Inc.
    TIM HORTON'S
    1234567 ONTARIO INC. O/A TIM HORTONS

This module produces a deterministic `employer_key` that collapses those into
one. It is rule-based on purpose: every transformation here is reversible
reasoning a human can check, and none of it can merge two genuinely different
companies the way a similarity threshold can. The raw string is never
destroyed — it is stored alongside, and `/employers/{key}/variants` exposes
exactly which raw strings landed on a key.

A fuzzy pass (pg_trgm similarity, scoped within a province) is deliberately
future work. It would catch transpositions like 'TIM HORTNOS' that this cannot,
at the cost of a tuned threshold and the possibility of a wrong merge. The
schema is shaped to accept it without a migration: `employer_entities.match_method`
already records how each grouping was arrived at.
"""

from __future__ import annotations

import re

from ingest.normalize.text import clean, squash, strip_accents

# Corporate form indicators, stripped only from the *end* of a name. 'Limited'
# in 'Seabase Newfoundland Limited' is noise; in 'Limited Edition Salon' it is
# the business. Anchoring to the tail is what keeps that distinction.
_LEGAL_SUFFIXES = (
    "INCORPORATED",
    "INCORPOREE",
    "INC",
    "LIMITED",
    "LIMITEE",
    "LTEE",
    "LTD",
    "CORPORATION",
    "CORP",
    "COMPANY",
    "LLP",
    "LLC",
    "ULC",
    "SENC",
    "SEC",
    "SRL",
    "ENR",
    "CIE",
)

# 'Operating as' markers. The trade name follows, and it is the name a person
# would recognise, so it wins over the numbered holding company in front of it.
_TRADE_NAME_SPLIT = re.compile(
    r"\b(?:"
    r"O\s*/\s*A|D\s*/?\s*B\s*/?\s*A|"
    r"OPERATING\s+AS|TRADING\s+AS|CARRYING\s+ON\s+BUSINESS\s+AS|"
    r"C\.?\s*O\.?\s*B\.?(?:\s+AS)?|"
    # 'c/o' is strictly 'care of', but in this dataset it is used the same way
    # as 'o/a' — 'Tims Kamloops Coffee Shop Inc c/o Tim Hortons'.
    r"C\s*/\s*O|"
    r"FAISANT\s+AFFAIRE\s+SOUS|S\s*/\s*L\s*N"
    r")\b",
    re.IGNORECASE,
)

# Franchise and branch numbering: '#4021', 'STORE 122', 'UNIT #7', 'NO. 45'.
_BRANCH_NUMBER = re.compile(
    r"\b(?:STORE|BRANCH|UNIT|LOCATION|SITE|NO|NUM|NUMBER)?\s*#\s*\d+\b|"
    r"\b(?:STORE|BRANCH|UNIT|LOCATION|SITE)\s+\d+\b",
    re.IGNORECASE,
)

_AMPERSAND = re.compile(r"\s*&\s*")

# Apostrophes and periods are *deleted*, not spaced out. Replacing them would
# split words that the writer joined: "TIM HORTON'S" has to reach the same key
# as "TIM HORTONS", and "A.B.C." the same key as "ABC". Every other separator
# becomes a space, because a hyphen or comma genuinely divides two words.
_INTRAWORD_PUNCT = re.compile(r"['.]+")
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")
_SUFFIX_TAIL = re.compile(rf"\s+(?:{'|'.join(_LEGAL_SUFFIXES)})$")

# A name that is only a corporation number carries no identity of its own.
_NUMBERED_COMPANY = re.compile(r"^\d{5,}(?:\s+(?:CANADA|[A-Z]{2}))?$")


def split_trade_name(value: object) -> tuple[str | None, str | None]:
    """Separate the legal entity from the trade name it operates under.

    '1317518 Alberta Ltd. o/a Tim Hortons' is one numbered company running one
    Tim Hortons outlet. Both halves are real and they answer different
    questions, so neither is discarded: the legal name identifies *who was
    issued the LMIA*, the trade name identifies *what brand they operate*.

    Returns (legal, trade); trade is None when the name carries no marker.
    """
    raw = clean(value)
    if raw is None:
        return None, None
    parts = _TRADE_NAME_SPLIT.split(raw)
    if len(parts) < 2:
        return raw, None
    legal = squash(parts[0]) or None
    trade = squash(parts[-1]) or None
    return (legal or raw), trade


def _normalize(text: str) -> str:
    """The shared reduction: casing, accents, punctuation, legal suffixes."""
    text = strip_accents(text).upper()
    text = _BRANCH_NUMBER.sub(" ", text)
    text = _AMPERSAND.sub(" AND ", text)
    text = _INTRAWORD_PUNCT.sub("", text)
    text = _NON_ALNUM.sub(" ", text)
    text = squash(text)

    # Repeatedly, because 'HOLDINGS LTD INC' and 'X CO LTD' both occur.
    previous = None
    while previous != text:
        previous = text
        text = squash(_SUFFIX_TAIL.sub("", text))

    return text


def make_key(value: object) -> str | None:
    """The legal-entity key: who the LMIA was actually issued to.

    Deliberately does *not* collapse a franchisee into its brand. The thirteen
    'Tim Hortons' spellings in the 2025–26 files are thirteen separate
    companies — 1317518 Alberta Ltd., Adams 22 Holdings Ltd., and so on — and
    merging them would report independent employers as one. The trade name is
    kept separately by `make_brand_key` for the times you do want the brand
    view.

    A bare corporation number keeps its own key: it is a legal identifier
    rather than a name, so it stays addressable, but two different numbers are
    never allowed to collapse together.
    """
    legal, _trade = split_trade_name(value)
    if legal is None:
        return None
    # Only the legal half. '1317518 Alberta Ltd.' and '1317518 Alberta Ltd. o/a
    # Tim Hortons' are the same company filing two rows, so they belong on the
    # same key; the brand they operate is carried by `make_brand_key` instead.
    return _normalize(legal) or None


def make_brand_key(value: object) -> str | None:
    """The brand key: what a customer would see on the sign.

    Falls back to the legal key when no trade name is declared, so grouping by
    brand never silently drops the employers that have only one name.
    """
    legal, trade = split_trade_name(value)
    if legal is None:
        return None
    return _normalize(trade) if trade else (_normalize(legal) or None)


def canonical_name(variants: list[str]) -> str:
    """Pick the display name for a group of raw spellings.

    The most frequent spelling wins; ties break toward the longest, then
    alphabetically, so the result is stable across reloads rather than
    dependent on row order.
    """
    if not variants:
        return ""
    counts: dict[str, int] = {}
    for v in variants:
        counts[v] = counts.get(v, 0) + 1
    return max(sorted(counts), key=lambda v: (counts[v], len(v)))
