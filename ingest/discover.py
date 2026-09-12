"""Finding the source files and working out where the data in them starts.

These are spreadsheets formatted for a human reader, not data exports. Each one
is a title banner, then the real header row, then the data, then a block of
footnotes. Handing the file straight to `read_csv` makes the banner a column
name and the footnotes eight more rows of data — which is what the first run of
`make inspect` did before this module existed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd

# Government CSV exports are frequently cp1252 rather than UTF-8, and reading
# one as the other yields mojibake instead of an error — which is worse, since
# it corrupts employer names silently. Strict UTF-8 first; fall back only on a
# genuine decode failure.
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

FORMATS = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xls"}

# Where a quarter ships in more than one format, this is the order of trust.
# CSV is a plainer export with fewer ways to lose a value.
FORMAT_PREFERENCE = ("csv", "xlsx", "xls")

QUARTER_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})[\s_\-]*[Qq](?P<q>[1-4])"),
    re.compile(r"[Qq](?P<q>[1-4])[\s_\-]*(?P<year>20\d{2})"),
)

# The banner states the classification vintage outright: '… National
# Occupational Classification (NOC) 2021 and Business Location, April to June
# 2025'. Per footnote 4 this is authoritative for the whole file.
_BANNER_NOC = re.compile(r"\bNOC\)?\s*(?P<version>20\d{2})\b", re.IGNORECASE)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip
_BANNER_PERIOD = re.compile(
    rf"\b(?P<month>{'|'.join(_MONTHS)})\s+(?:to|-)\s+"
    rf"(?P<end_month>{'|'.join(_MONTHS)})\s+(?P<year>20\d{{2}})\b",
    re.IGNORECASE,
)


def is_source_file(path: Path) -> bool:
    """Excel writes a '~$name.xlsx' lock file beside any open workbook, and macOS
    leaves '._name' AppleDouble stubs on non-native volumes. Neither is data."""
    return (
        path.is_file()
        and path.suffix.lower() in FORMATS
        and not path.name.startswith(("~$", "._", "."))
        and path.stat().st_size > 0
    )


def parse_quarter(name: str) -> str | None:
    for pattern in QUARTER_PATTERNS:
        if m := pattern.search(name):
            return f"{m.group('year')}Q{m.group('q')}"
    return None


def quarter_from_banner(banner: str | None) -> str | None:
    """Derive the quarter from the period the banner names.

    A fallback for archive filenames that do not carry one. 'April to June 2025'
    is 2025Q2 — the fiscal-year labelling the publisher uses elsewhere does not
    apply to these banners, which name calendar months.
    """
    if not banner or not (m := _BANNER_PERIOD.search(banner)):
        return None
    month = _MONTHS[m.group("month").lower()]
    return f"{m.group('year')}Q{(month - 1) // 3 + 1}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Layout:
    """Where the meaningful parts of a human-formatted sheet actually are."""

    banner: str | None
    noc_version: str | None
    header_row: int
    columns: list[object]
    data: pd.DataFrame  # index preserved: it is the row number within the sheet
    notes: list[str] = field(default_factory=list)


@dataclass
class SourceFile:
    path: Path
    fmt: str
    quarter: str | None
    siblings: list[Path] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name


def read_raw(path: Path, fmt: str) -> tuple[pd.DataFrame, str]:
    """Read with no header at all, so the banner and notes stay visible."""
    if fmt != "csv":
        # Annotated because pandas types `engine` as a Literal union, and a
        # plain str does not narrow to it.
        engine: Literal["openpyxl", "xlrd"] = "openpyxl" if fmt == "xlsx" else "xlrd"
        return pd.read_excel(path, dtype=str, header=None, engine=engine), engine

    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            frame = pd.read_csv(
                path, dtype=str, header=None, encoding=encoding, on_bad_lines="warn"
            )
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_error = exc
            continue
        return frame, encoding
    raise RuntimeError(f"no encoding in {ENCODINGS} could read {path.name}") from last_error


def detect_layout(frame: pd.DataFrame) -> Layout:
    """Split a sheet into banner / header / data / notes.

    The structural tell is width. A real data row has a value in every column;
    the banner and every footnote occupy column 0 alone with the rest of the
    row empty. So the header is the first near-fully-populated row, and the
    notes are what trails off after the last one.
    """
    empty = Layout(banner=None, noc_version=None, header_row=0, columns=[], data=frame.iloc[0:0])
    if frame.empty:
        return empty

    width = frame.shape[1]
    threshold = max(2, width - 1)
    populated = frame.notna().sum(axis=1)

    candidates = populated[populated >= threshold]
    if candidates.empty:
        return empty
    header_row = int(candidates.index[0])

    banner_cells = [str(v).strip() for v in frame.iloc[:header_row, 0].dropna()]
    banner = banner_cells[0] if banner_cells else None
    noc_version = m.group("version") if banner and (m := _BANNER_NOC.search(banner)) else None

    columns = list(frame.iloc[header_row])

    body = frame.iloc[header_row + 1 :]
    data_mask = body.notna().sum(axis=1) >= threshold
    data = body[data_mask]

    last_data = int(data.index.max()) if len(data) else header_row
    trailing = body.loc[body.index > last_data]
    notes = [str(v).strip() for v in trailing.iloc[:, 0].dropna() if str(v).strip()]

    return Layout(
        banner=banner,
        noc_version=noc_version,
        header_row=header_row,
        columns=columns,
        data=data,
        notes=notes,
    )


def discover(raw_dir: Path) -> list[SourceFile]:
    """List the files to load, one per quarter.

    Where a quarter ships in several formats, the most trusted one is loaded
    and the rest are recorded as siblings so the loader can cross-check their
    row counts. Files whose quarter cannot be parsed from the filename are
    still returned — `load` recovers the quarter from the banner instead.
    """
    paths = sorted(p for p in raw_dir.rglob("*") if is_source_file(p))

    by_quarter: dict[str, list[Path]] = {}
    unkeyed: list[SourceFile] = []
    for path in paths:
        quarter = parse_quarter(path.name)
        if quarter is None:
            unkeyed.append(SourceFile(path=path, fmt=FORMATS[path.suffix.lower()], quarter=None))
        else:
            by_quarter.setdefault(quarter, []).append(path)

    chosen: list[SourceFile] = []
    for quarter, group in sorted(by_quarter.items()):
        ranked = sorted(group, key=lambda p: FORMAT_PREFERENCE.index(FORMATS[p.suffix.lower()]))
        primary, *rest = ranked
        chosen.append(
            SourceFile(
                path=primary,
                fmt=FORMATS[primary.suffix.lower()],
                quarter=quarter,
                siblings=rest,
            )
        )

    return chosen + unkeyed
