"""Shared text cleanup.

Every string column in these files carries formatting noise from the
spreadsheet it was exported from: cells padded to a fixed width, curly quotes
from Word, non-breaking spaces. None of that is data.
"""

from __future__ import annotations

import re
import unicodedata

# Typographic characters that Excel substitutes silently. Left as-is they make
# 'St. John's' and 'St. John's' two different employers.
_PUNCT_FOLD = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        " ": " ",
        " ": " ",
        " ": " ",
        "﻿": "",
    }
)

_WHITESPACE = re.compile(r"\s+")


def clean(value: object) -> str | None:
    """Fold typographic variants, collapse whitespace, and empty-to-None.

    This is the only cleanup applied before a value is stored as `*_raw`. It
    removes noise introduced by the export, never anything the publisher
    actually typed.
    """
    if value is None:
        return None
    text = str(value)
    if text.strip().lower() in {"", "nan", "none", "n/a", "na", "-", "--"}:
        return None
    text = unicodedata.normalize("NFKC", text).translate(_PUNCT_FOLD)
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def strip_accents(text: str) -> str:
    """Fold accented characters to ASCII.

    Used for matching keys only — never for stored display values. 'Montréal'
    and 'Montreal' are the same city, but only one of them is how the employer
    spelled it.
    """
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def squash(text: str) -> str:
    """Collapse internal whitespace runs to single spaces and trim."""
    return _WHITESPACE.sub(" ", text).strip()
