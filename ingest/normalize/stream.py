"""Program stream normalization.

Same fixed-width padding as the province column, plus a French label sitting in
the English file ('Talent mondial' for Global Talent Stream). The older
quarters use a different vocabulary again — the pre-2016 files split on skill
level rather than wage, and the caregiver streams were separate — so unknown
values pass through as None rather than being rejected, and the loader counts
them so a new label shows up in the ingest report instead of vanishing.
"""

from __future__ import annotations

from ingest.normalize.text import clean, strip_accents

HIGH_WAGE = "High Wage"
LOW_WAGE = "Low Wage"
PRIMARY_AGRICULTURE = "Primary Agriculture"
GLOBAL_TALENT = "Global Talent Stream"
PR_ONLY = "Permanent Resident Only"

_ALIASES = {
    # Current vocabulary, as seen in 2025Q2–2026Q1.
    "high wage": HIGH_WAGE,
    "low wage": LOW_WAGE,
    "primary agriculture": PRIMARY_AGRICULTURE,
    "global talent stream": GLOBAL_TALENT,
    "talent mondial": GLOBAL_TALENT,
    "permanent resident only": PR_ONLY,
    # Variants and older labels, pending confirmation against the archive.
    "high-wage": HIGH_WAGE,
    "low-wage": LOW_WAGE,
    "higher-skilled": HIGH_WAGE,
    "lower-skilled": LOW_WAGE,
    "agricultural stream": PRIMARY_AGRICULTURE,
    "primary agriculture stream": PRIMARY_AGRICULTURE,
    "seasonal agricultural worker program": PRIMARY_AGRICULTURE,
    "sawp": PRIMARY_AGRICULTURE,
    "permanent residence only": PR_ONLY,
    "pr only": PR_ONLY,
}

CANONICAL = (HIGH_WAGE, LOW_WAGE, PRIMARY_AGRICULTURE, GLOBAL_TALENT, PR_ONLY)


def normalize(value: object) -> str | None:
    """Map a raw stream label to its canonical form, or None if unrecognised."""
    raw = clean(value)
    if raw is None:
        return None
    folded = strip_accents(raw).lower().strip(" .")
    return _ALIASES.get(folded)


def is_pr_only(canonical: str | None) -> bool:
    """Whether this row came from the PR-only inclusion introduced at 2023Q4.

    Stored per row so a time series can be filtered to a like-for-like basis
    rather than merely annotated. See app/api/trends.py.
    """
    return canonical == PR_ONLY
