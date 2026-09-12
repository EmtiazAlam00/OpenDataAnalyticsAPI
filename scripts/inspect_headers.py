"""Report the real shape of every file in data/raw/.

Nothing downstream of this is designed yet on purpose. The schema, the column
mapping registry and the normalizers all depend on what the published files
actually contain, and the spec's own field list is hedged with "approximately".
So this reads every file, reports what it found, and asserts nothing.

These are formatted-for-humans spreadsheets, not data exports. Each one is a
title banner, then the real header row, then the data, then a block of
footnotes. The layout detection here is the first draft of what will become
ingest/discover.py.

Run it with `make inspect` (in the container) or `python -m scripts.inspect_headers`.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.config import settings

# Government CSV exports are frequently cp1252 rather than UTF-8, and reading
# one as the other produces mojibake instead of an error — which is worse,
# because it corrupts employer names silently. Try strict UTF-8 first and fall
# back only on a genuine decode failure.
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

FORMATS = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xls"}

QUARTER_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})[\s_\-]*[Qq](?P<q>[1-4])"),
    re.compile(r"[Qq](?P<q>[1-4])[\s_\-]*(?P<year>20\d{2})"),
)


def is_source_file(path: Path) -> bool:
    """Excel writes a '~$name.xlsx' lock file next to any open workbook, and macOS
    litters '._name' AppleDouble stubs onto non-native volumes. Neither is data."""
    return (
        path.suffix.lower() in FORMATS
        and not path.name.startswith(("~$", "._", "."))
        and path.stat().st_size > 0
    )


@dataclass
class Layout:
    """Where the meaningful parts of a human-formatted sheet actually are."""

    banner: str | None
    header_row: int
    columns: list[str]
    data_rows: int
    notes: list[str]


@dataclass
class FileReport:
    path: Path
    fmt: str
    quarter: str | None
    reader: str | None = None
    layout: Layout | None = None
    error: str | None = None

    @property
    def shape_key(self) -> tuple[str, ...]:
        cols = self.layout.columns if self.layout else []
        return tuple(re.sub(r"\s+", " ", c).strip().lower() for c in cols)


def parse_quarter(name: str) -> str | None:
    for pattern in QUARTER_PATTERNS:
        if m := pattern.search(name):
            return f"{m.group('year')}Q{m.group('q')}"
    return None


def read_raw(path: Path, fmt: str) -> tuple[pd.DataFrame, str]:
    """Read with no header at all, so the banner and notes stay visible."""
    if fmt != "csv":
        engine = "openpyxl" if fmt == "xlsx" else "xlrd"
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
    raise RuntimeError(f"no encoding in {ENCODINGS} could read this file") from last_error


def detect_layout(frame: pd.DataFrame) -> Layout:
    """Split a sheet into banner / header / data / notes.

    The structural tell is column 1. A real data row has a value in every
    column; the banner and every footnote occupy column 0 alone with the rest
    of the row empty. So the header is the first fully-populated row, and the
    notes are whatever trails off after the last one.
    """
    if frame.empty:
        return Layout(banner=None, header_row=0, columns=[], data_rows=0, notes=[])

    populated = frame.notna().sum(axis=1)
    width = frame.shape[1]

    # First row that fills most of the sheet's width is the header.
    candidates = populated[populated >= max(2, width - 1)]
    if candidates.empty:
        return Layout(banner=None, header_row=0, columns=[], data_rows=0, notes=[])
    header_row = int(candidates.index[0])

    banner_cells = [str(v).strip() for v in frame.iloc[:header_row, 0].dropna()]
    banner = banner_cells[0] if banner_cells else None

    columns = [str(v).strip() for v in frame.iloc[header_row] if pd.notna(v)]

    body = frame.iloc[header_row + 1 :]
    body_populated = body.notna().sum(axis=1)
    data_mask = body_populated >= max(2, width - 1)
    data_rows = int(data_mask.sum())

    # Everything after the last real data row that still has a column-0 value.
    last_data = data_mask[data_mask].index.max() if data_rows else header_row
    trailing = body.loc[body.index > last_data]
    notes = [str(v).strip() for v in trailing.iloc[:, 0].dropna() if str(v).strip()]

    return Layout(
        banner=banner, header_row=header_row, columns=columns, data_rows=data_rows, notes=notes
    )


def inspect(path: Path) -> FileReport:
    fmt = FORMATS[path.suffix.lower()]
    report = FileReport(path=path, fmt=fmt, quarter=parse_quarter(path.name))
    try:
        frame, report.reader = read_raw(path, fmt)
        report.layout = detect_layout(frame)
    except Exception as exc:  # noqa: BLE001 — this is a survey, not a pipeline
        report.error = f"{type(exc).__name__}: {exc}"
    return report


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> int:
    raw_dir = settings.raw_data_dir
    if not raw_dir.is_dir():
        print(f"No such directory: {raw_dir.resolve()}", file=sys.stderr)
        return 1

    paths = sorted(p for p in raw_dir.rglob("*") if p.is_file() and is_source_file(p))
    if not paths:
        print(f"No source files found under {raw_dir.resolve()}", file=sys.stderr)
        return 1

    reports = [inspect(p) for p in paths]

    rule(f"{len(reports)} files under {raw_dir.resolve()}")
    print()
    for r in reports:
        quarter = r.quarter or "UNPARSED"
        if r.error or not r.layout:
            print(f"  {quarter:>9}  {r.fmt:<4}  {r.path.name}\n             !! {r.error}")
            continue
        lay = r.layout
        print(
            f"  {quarter:>9}  {r.fmt:<4}  {lay.data_rows:>7,} data rows  "
            f"hdr@{lay.header_row}  {len(lay.columns):>2} cols  "
            f"{len(lay.notes):>2} notes  {r.path.name}"
        )

    # Distinct header shapes — the drift the mapping registry has to absorb.
    shapes: dict[tuple[str, ...], list[FileReport]] = defaultdict(list)
    for r in reports:
        if r.layout and r.layout.columns:
            shapes[r.shape_key].append(r)

    rule(f"{len(shapes)} distinct header shapes")
    for i, (shape, members) in enumerate(sorted(shapes.items(), key=lambda kv: -len(kv[1])), 1):
        quarters = sorted({m.quarter or m.path.name for m in members})
        print(f"\n  Shape {i} — {len(members)} file(s): {', '.join(quarters)}")
        for col in shape:
            print(f"      · {col}")

    # Banners carry the NOC vintage and the period covered, and they drift too.
    rule("Title banners")
    print()
    for r in reports:
        if r.layout and r.layout.banner:
            print(f"  {r.quarter or r.path.name}: {r.layout.banner}")

    # The footnotes are the publisher's own caveats. They belong in /about
    # verbatim rather than paraphrased, and a change in them is a change in
    # what the data means.
    note_sources: dict[str, list[str]] = defaultdict(list)
    for r in reports:
        for note in r.layout.notes if r.layout else []:
            note_sources[note].append(r.quarter or r.path.name)

    rule(f"{len(note_sources)} distinct footnotes")
    for note, quarters in note_sources.items():
        tag = "ALL" if len(quarters) == len(reports) else ", ".join(sorted(quarters))
        print(f"\n  [{tag}]\n  {note}")

    # Cross-format disagreement: where a quarter ships more than one format,
    # the row counts should match. If they do not, one of the publisher's own
    # exports is lossy and we need to know which to trust.
    by_quarter: dict[str, list[FileReport]] = defaultdict(list)
    for r in reports:
        if r.quarter and r.layout:
            by_quarter[r.quarter].append(r)

    multi = {q: rs for q, rs in by_quarter.items() if len(rs) > 1}
    conflicts = {
        q: rs for q, rs in multi.items() if len({r.layout.data_rows for r in rs if r.layout}) > 1
    }

    rule("Coverage")
    print(f"\n  {len(multi)} quarters ship in more than one format; {len(conflicts)} disagree.")
    for quarter, rs in sorted(conflicts.items()):
        detail = ", ".join(
            f"{r.fmt}={r.layout.data_rows:,}" for r in sorted(rs, key=lambda r: r.fmt) if r.layout
        )
        print(f"      {quarter}: {detail}")

    parsed = sorted(by_quarter)
    if parsed:
        total = sum(r.layout.data_rows for rs in by_quarter.values() for r in rs[:1] if r.layout)
        print(f"  Quarters: {parsed[0]} … {parsed[-1]} ({len(parsed)} total, {total:,} data rows)")

    unparsed = [r.path.name for r in reports if not r.quarter]
    if unparsed:
        print(f"  Filenames with no parseable quarter ({len(unparsed)}):")
        for name in unparsed:
            print(f"      {name}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
