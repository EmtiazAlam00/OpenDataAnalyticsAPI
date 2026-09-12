"""Load the quarterly files into Postgres.

    python -m scripts.load                 # every file in data/raw/
    python -m scripts.load --file 2025Q2.xlsx
    python -m scripts.load --report        # show what is loaded, change nothing

Safe to re-run: rows are upserted on (source_file, line_no), so a second pass
over the same files leaves the database unchanged rather than doubling it.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import settings
from app.db import SessionLocal
from ingest import discover, loader


def print_run(run) -> None:  # noqa: ANN001 — IngestRun, avoiding a circular import at module load
    flag = "ok " if run.status == "ok" else "FAIL"
    print(
        f"  [{flag}] {run.quarter or '??????'}  {run.source_file:<28} "
        f"read={run.rows_read:>7,}  loaded={run.rows_loaded:>7,}  rejected={run.rows_rejected:>5,}"
    )
    if run.error:
        print(f"         !! {run.error}")
    for key, value in sorted((run.anomalies or {}).items()):
        if key.endswith("_samples"):
            print(f"         · {key}: {', '.join(map(str, value[:5]))}")
        else:
            print(f"         · {key}: {value}")
    if run.unmapped_columns:
        cols = ", ".join(c["header"] for c in run.unmapped_columns)
        print(f"         · unmapped columns: {cols}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="load only this filename from the raw data directory")
    parser.add_argument("--report", action="store_true", help="summarise what is loaded and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    with SessionLocal() as session:
        if args.report:
            for key, value in loader.summary(session).items():
                print(f"  {key}: {value}")
            return 0

        if args.file:
            sources = [s for s in discover.discover(settings.raw_data_dir) if s.name == args.file]
            if not sources:
                print(f"No such file under {settings.raw_data_dir}: {args.file}", file=sys.stderr)
                return 1
            runs = [loader.load_file(session, sources[0])]
            loader.resolve_entities(session)
        else:
            runs = loader.load_all(session, settings.raw_data_dir)

        if not runs:
            print(f"No source files found under {settings.raw_data_dir.resolve()}", file=sys.stderr)
            return 1

        print(f"\n{len(runs)} file(s):\n")
        for run in runs:
            print_run(run)

        print("\nTotals:")
        for key, value in loader.summary(session).items():
            print(f"  {key}: {value}")
        print()

        return 0 if all(r.status == "ok" for r in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
