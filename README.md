# TFWP Open Data API

A queryable API over the Government of Canada **Temporary Foreign Worker Program —
Positive LMIA Employers List**: quarterly spreadsheets formatted for human readers,
turned into one normalized schema with search and derived analytics on top.

```
FastAPI · PostgreSQL · pandas · SQLAlchemy 2.0 · Docker
104 tests · 45,078 rows · 0 rows silently dropped
```

> Contains information licensed under the Open Government Licence – Canada.

---

## Quick start

```bash
cp .env.example .env
make up                 # api + postgres, on 8001/5433
make migrate
make load               # ingest everything in data/raw/
```

| | |
|---|---|
| **Dashboard** | http://localhost:8001/dash |
| **Swagger** | http://localhost:8001/docs |
| **Health** | http://localhost:8001/health |
| **Caveats** | http://localhost:8001/about |
| **Ingest report** | http://localhost:8001/meta/ingest |

Ports are offset from the defaults so this stack can run beside another local
Postgres/API on 5432/8000. Change them in `.env`.

## The problem this solves

The source is not a data export. Each quarterly file is a title banner, a header
row, the data, and then a block of footnotes — so handing it to `read_csv` makes
the banner a column name and the footnotes eight extra rows of data. On top of
that:

| What the files do | What the loader does |
|---|---|
| `Ontario` appears as 3+ strings, padded to varying widths | Normalized to a two-letter code |
| A non-region (*"head office outside of Canada"*) sits in the province column | Kept, but scoped so it can never appear as a 14th province |
| `Talent mondial` appears in the English file beside `Global Talent Stream` | Mapped to one canonical stream |
| NOC code and title are packed into one cell | Split on the first hyphen only, so hyphenated titles survive |
| 31,843 distinct employer spellings across 45,078 rows | Resolved to legal entities, with the raw string preserved |
| ~1,400 postal codes are truncated by the publisher's own export | FSA extracted, truncation flagged |
| Footnotes drift between quarters | Captured per file and served from `/meta/ingest` |

## Three guarantees, each with a test behind it

**1. Re-running the loader is a no-op.** Rows upsert on `(source_file, line_no)` —
not a content hash, because two identical rows in one quarter are two genuinely
separate LMIAs. Rows are stamped with the current run id and anything left over
from a previous run of the same file is deleted, so reloading a file that has
*shrunk* is idempotent too, not merely additive.

**2. Nothing disappears quietly.** A row that cannot be loaded goes to
`ingest_rejects` with its raw contents and a reason. A *value* that cannot be
normalized does not cost the row — it becomes NULL beside its preserved `*_raw`
string and increments a counter on the run. `rows_read == rows_loaded +
rows_rejected` for every file, asserted in `tests/ingest/`.

**3. Any grouping is inspectable.** `/entities/{id}` lists every raw spelling
merged into an entity. A name-matching rule you cannot audit is just an
unfalsifiable claim about the data.

## Employer identity

Footnote 3 of every file states the problem: *"The employer name is manually
entered… subject to potential data entry error and inconsistent spelling."*

Two different questions get two different answers, and `/stats/top-employers`
makes you choose with `group_by`:

- **`entity`** — the legal employer the LMIA was issued to. `Tim Hortons Inc.`,
  `TIM HORTON'S` and `TIM HORTONS #4021` collapse; `1317518 Alberta Ltd. o/a Tim
  Hortons` does **not**, because a franchisee is a different company from its
  franchisor.
- **`brand`** — grouped by the name traded under, so the 14 separate companies
  running Tim Hortons outlets count once.
- **`raw`** — the published strings untouched. The honest baseline, and the way
  to see exactly what normalization changed.

Matching is deterministic — rules plus exact matching on the normalized key — so
no two distinct companies can be merged. A `pg_trgm` similarity pass is future
work; `employer_entities.match_method` already records how each grouping was
made, so it can be added without a migration.

## Reading the numbers

`GET /about` carries the full set of caveats. The short version:

- **Incomplete by construction.** Employers whose business name is a personal
  name are not published. Every count is a floor, not a total.
- **Positions, not people.** A positive LMIA authorises a search. It does not
  mean a permit was issued or anyone arrived.
- **Occupation codes.** Footnote 4 records that ESDC retroactively converted
  pre-September-2024 occupations to NOC 2021 using StatCan concordance, so the
  vintage is a property of *when a file was published*, not of the quarter it
  describes. Taken from each file's own banner and stored per row.
- **Structural break at 2023Q4**, when LMIAs supporting Permanent Residence
  began to be included and earlier lists were not revised. `/trends/positions`
  reports it in `series_breaks` and sets `comparable: false` rather than handing
  back a series that silently changes basis. `exclude_pr_only=true` removes the
  discontinuity at source.

## Dashboard

`/dash` is a single static HTML file served by the API — no build step, no
second process, no CORS to configure, because it reads this API over
same-origin `fetch`. Quarter / province / PR-only filters, the top-employers
chart with a live **entity vs brand vs raw** toggle, positions by quarter,
provincial breakdown, the ingest report, and the caveats.

It is deliberately a *reader* of the public endpoints, so it cannot show a
number the API would not also give you — a test asserts it references only
paths that exist.

## Endpoints

| | |
|---|---|
| `GET /employers` | Search and filter rows; paginated |
| `GET /employers/{id}` | One published row |
| `GET /entities` | Resolved legal employers |
| `GET /entities/{id}` | One employer, with every spelling merged into it |
| `GET /quarters` | What is loaded, and what it is comparable with |
| `GET /stats/top-employers` | `group_by=entity\|brand\|raw`, `metric=positions\|lmias` |
| `GET /stats/by-province` | Non-province bucket reported separately |
| `GET /stats/by-occupation` | Always reports the NOC vintage |
| `GET /stats/by-stream` | Including rows whose stream did not map |
| `GET /trends/positions` | With `series_breaks` |
| `GET /meta/ingest` | Per-file tallies, anomalies, publisher footnotes |
| `GET /about`, `/attribution` | Caveats and licence |
| `GET /dash` | Dashboard — reads the endpoints above, adds nothing of its own |

## Source data

Place the quarterly files in `data/raw/` — gitignored, and bind-mounted
read-only into the container. `.csv`, `.xlsx` and `.xls` are all read. Discovery
groups by quarter and prefers CSV, falling back to XLSX then XLS, so no quarter
is lost to a missing format; where a quarter ships more than one, row counts are
cross-checked and disagreements reported.

- **Portal:** https://open.canada.ca/en
- **Dataset id:** `90fed587-1364-4f33-a9ee-208181dc0b97`

```bash
make inspect            # survey headers, encodings, layouts, coverage gaps
```

**Currently loaded: 2025Q2 – 2026Q1.** The column mapping registry
(`ingest/headers.py`) matches headers by alias rather than declaring one rigid
shape per file, so the 2018–2024 archive can be backfilled by adding aliases
rather than rewriting the loader. Unrecognised columns are reported in
`/meta/ingest`, never dropped.

## Development

```bash
make dev                # .venv via uv, editable install with dev extras
make test               # 104 tests; integration tests skip if the db is empty
make lint typecheck     # ruff + mypy, both clean
make psql
```

Source directories are bind-mounted into the API container, so edits and
generated migrations are live on both sides without a rebuild.

## Attribution

> Contains information licensed under the Open Government Licence – Canada.

Reproduced and adapted under the [Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada).
This project is not endorsed by or affiliated with the Government of Canada.
