"""Column mapping registry.

The whole point of this file is that adding support for a new quarterly layout
is a data change, not a code change. Headers are matched by alias rather than
by declaring one rigid shape per file, so a quarter that renames a column or
reorders them needs one new string in the table below — and a quarter that
introduces a genuinely new column fails loudly in `tests/ingest/` rather than
being dropped in silence.

Known state of the world, from `make inspect` over 2025Q2–2026Q1:

    Province/Territory · Program Stream · Employer · Address
    Occupation · Incorporate Status · Approved LMIAs · Approved Positions

All four of those files share one shape. The alias sets below are wider than
that on purpose: the 2018–2024 archive has not been ingested yet, and the
likely drift (French headers, 'Positions Approved' word order, NOC split across
two columns) is cheaper to anticipate than to retrofit. Anything unanticipated
still surfaces — see `map_columns`, which reports unmapped headers rather than
ignoring them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ingest.normalize.text import strip_accents

PROVINCE = "province"
PROGRAM_STREAM = "program_stream"
EMPLOYER = "employer"
ADDRESS = "address"
OCCUPATION = "occupation"
NOC_CODE = "noc_code"
NOC_TITLE = "noc_title"
INCORPORATE_STATUS = "incorporate_status"
APPROVED_LMIAS = "approved_lmias"
APPROVED_POSITIONS = "approved_positions"

# Only the employer is structurally required: a row without one cannot be
# attributed to anybody and is rejected. Every other field degrades to NULL,
# because losing a province is a gap in one column while losing the row is a
# gap in every column.
REQUIRED = (EMPLOYER,)

ALIASES: dict[str, set[str]] = {
    PROVINCE: {
        "province/territory",
        "province or territory",
        "province territory",
        "province",
        "provinces/territoires",
        "province/territoire",
        "business location",
        "location",
    },
    PROGRAM_STREAM: {
        "program stream",
        "stream",
        "programstream",
        "volet du programme",
        "volet",
        "program",
    },
    EMPLOYER: {
        "employer",
        "employer name",
        "employer's name",
        "employeur",
        "nom de l'employeur",
        "name of employer",
        "business name",
    },
    ADDRESS: {
        "address",
        "employer address",
        "business address",
        "adresse",
        "adresse de l'employeur",
    },
    OCCUPATION: {
        "occupation",
        "occupations",
        "noc and occupation",
        "noc - occupation",
        "profession",
        "occupation (noc)",
    },
    # Older layouts may split what 2025-26 packs into one cell. When both a
    # split code and a packed occupation are present, the split columns win.
    NOC_CODE: {
        "noc",
        "noc code",
        "noc 2011",
        "noc 2021",
        "national occupational classification",
        "code cnp",
        "cnp",
    },
    NOC_TITLE: {
        "noc title",
        "occupation title",
        "job title",
        "titre de la profession",
    },
    INCORPORATE_STATUS: {
        "incorporate status",
        "incorporation status",
        "incorporated status",
        "statut de constitution",
    },
    APPROVED_LMIAS: {
        "approved lmias",
        "approved lmia",
        "number of approved lmias",
        "lmias approved",
        "positive lmias",
        "edsc approuvees",
        "emt approuvees",
    },
    APPROVED_POSITIONS: {
        "approved positions",
        "positions approved",
        "number of approved positions",
        "number of positions",
        "approved number of positions",
        "positions",
        "postes approuves",
        "nombre de postes approuves",
    },
}

_LOOKUP: dict[str, str] = {
    alias: canonical for canonical, aliases in ALIASES.items() for alias in aliases
}

_PUNCT = re.compile(r"[^a-z0-9/' ]+")
_SPACE = re.compile(r"\s+")


def fold(header: object) -> str:
    """Reduce a raw header cell to its lookup form.

    Absorbs the fixed-width padding, casing and accent differences that make
    'Province/Territory' and 'province/territoire ' distinct strings.
    """
    text = strip_accents(str(header)).lower()
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


@dataclass
class ColumnMap:
    """Which sheet column supplies each canonical field."""

    indexes: dict[str, int] = field(default_factory=dict)
    unmapped: list[tuple[int, str]] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_required

    @property
    def has_split_noc(self) -> bool:
        """True when the file supplies NOC code separately from the occupation text."""
        return NOC_CODE in self.indexes

    def index_of(self, canonical: str) -> int | None:
        return self.indexes.get(canonical)


def map_columns(headers: list[object]) -> ColumnMap:
    """Resolve a file's header row against the registry.

    Duplicate mappings keep the first occurrence — some sheets repeat a column
    for print layout — and every header that matches nothing is reported, so a
    newly introduced column shows up in the ingest report instead of being
    quietly discarded.
    """
    result = ColumnMap()
    for position, header in enumerate(headers):
        folded = fold(header)
        if not folded:
            continue
        canonical = _LOOKUP.get(folded)
        if canonical is None:
            result.unmapped.append((position, str(header).strip()))
        elif canonical not in result.indexes:
            result.indexes[canonical] = position

    # An occupation column and a split NOC column are alternatives, not
    # requirements — but at least one of them has to be present for the row to
    # carry any occupation at all. That is a warning, not a rejection.
    result.missing_required = [f for f in REQUIRED if f not in result.indexes]
    return result
