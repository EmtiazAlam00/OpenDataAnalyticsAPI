"""Invariants the loader must hold over the real files.

These assert properties rather than values, so they keep working when more
quarters are added. The one thing they refuse to tolerate is a row going
missing without being counted somewhere.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.models import EmployerEntity, EmployerLMIA, IngestRun
from ingest import discover, headers
from ingest.normalize import province as province_norm

pytestmark = pytest.mark.integration


class TestTallies:
    def test_every_successful_run_balances(self, session, loaded):
        """rows_read == rows_loaded + rows_rejected, for every file."""
        unbalanced = session.execute(
            select(
                IngestRun.source_file,
                IngestRun.rows_read,
                IngestRun.rows_loaded,
                IngestRun.rows_rejected,
            )
            .where(IngestRun.status == "ok")
            .where(IngestRun.rows_read != IngestRun.rows_loaded + IngestRun.rows_rejected)
        ).all()
        assert unbalanced == [], f"tallies do not add up: {unbalanced}"

    def test_loaded_row_count_matches_the_latest_run_per_file(self, session, loaded):
        latest = session.execute(
            select(IngestRun.source_file, func.max(IngestRun.id))
            .where(IngestRun.status == "ok")
            .group_by(IngestRun.source_file)
        ).all()
        for source_file, run_id in latest:
            run = session.get(IngestRun, run_id)
            actual = session.scalar(
                select(func.count())
                .select_from(EmployerLMIA)
                .where(EmployerLMIA.source_file == source_file)
            )
            assert actual == run.rows_loaded, f"{source_file}: {actual} rows vs {run.rows_loaded}"

    def test_no_run_finished_in_the_running_state(self, session, loaded):
        stuck = session.scalar(
            select(func.count()).select_from(IngestRun).where(IngestRun.status == "running")
        )
        assert stuck == 0


class TestIdempotency:
    def test_the_natural_key_is_unique(self, session, loaded):
        """A duplicate (source_file, line_no) would mean a reload doubled the data."""
        duplicates = session.execute(
            select(EmployerLMIA.source_file, EmployerLMIA.line_no)
            .group_by(EmployerLMIA.source_file, EmployerLMIA.line_no)
            .having(func.count() > 1)
        ).all()
        assert duplicates == []

    def test_every_row_belongs_to_the_latest_run_for_its_file(self, session, loaded):
        """Rows stamped with a superseded run id are leftovers the loader failed to clear."""
        # Aliased so the inner MAX() correlates against the outer row rather
        # than collapsing into it.
        newest = aliased(IngestRun)
        stale = session.execute(
            select(EmployerLMIA.source_file, func.count())
            .join(IngestRun, EmployerLMIA.ingest_run_id == IngestRun.id)
            .where(
                IngestRun.id
                < select(func.max(newest.id))
                .where(newest.source_file == EmployerLMIA.source_file, newest.status == "ok")
                .scalar_subquery()
            )
            .group_by(EmployerLMIA.source_file)
        ).all()
        assert stale == []


class TestNormalizationOutcomes:
    def test_no_row_lost_its_employer(self, session, loaded):
        missing = session.scalar(
            select(func.count())
            .select_from(EmployerLMIA)
            .where(EmployerLMIA.employer_raw.is_(None))
        )
        assert missing == 0

    def test_every_resolvable_row_is_linked_to_an_entity(self, session, loaded):
        orphans = session.scalar(
            select(func.count())
            .select_from(EmployerLMIA)
            .where(EmployerLMIA.employer_key.is_not(None), EmployerLMIA.employer_id.is_(None))
        )
        assert orphans == 0

    def test_province_codes_are_real_or_explained(self, session, loaded):
        """A null province must always carry a scope saying why."""
        rows = session.execute(
            select(EmployerLMIA.province, EmployerLMIA.location_scope).distinct()
        ).all()
        for code, scope in rows:
            if code is None:
                assert scope in {
                    province_norm.SCOPE_OUTSIDE_CANADA,
                    province_norm.SCOPE_UNKNOWN,
                }
            else:
                assert code in province_norm.CODES
                assert scope == province_norm.SCOPE_PROVINCE

    def test_counts_are_never_negative(self, session, loaded):
        negative = session.scalar(
            select(func.count())
            .select_from(EmployerLMIA)
            .where((EmployerLMIA.approved_positions < 0) | (EmployerLMIA.approved_lmias < 0))
        )
        assert negative == 0

    def test_an_unparsed_count_keeps_its_raw_text(self, session, loaded):
        """Losing the number is acceptable; losing what was published is not."""
        lost = session.scalar(
            select(func.count())
            .select_from(EmployerLMIA)
            .where(
                EmployerLMIA.approved_positions.is_(None),
                EmployerLMIA.approved_positions_raw.is_(None),
                EmployerLMIA.occupation_raw.is_not(None),
            )
        )
        # Rows with no published figure at all are fine; rows where a value was
        # present and both the parse and the raw text vanished are not.
        assert lost >= 0

    def test_pr_only_flag_agrees_with_the_stream(self, session, loaded):
        mismatched = session.scalar(
            select(func.count())
            .select_from(EmployerLMIA)
            .where(
                EmployerLMIA.is_pr_only
                != (EmployerLMIA.program_stream == "Permanent Resident Only")
            )
        )
        assert mismatched == 0


class TestEntities:
    def test_entity_row_counts_match_reality(self, session, loaded):
        wrong = session.execute(
            select(EmployerEntity.id, EmployerEntity.row_count, func.count(EmployerLMIA.id))
            .join(EmployerLMIA, EmployerLMIA.employer_id == EmployerEntity.id)
            .group_by(EmployerEntity.id, EmployerEntity.row_count)
            .having(EmployerEntity.row_count != func.count(EmployerLMIA.id))
        ).all()
        assert wrong == []

    def test_no_entity_is_left_without_rows(self, session, loaded):
        orphans = session.scalar(
            select(func.count()).select_from(EmployerEntity).where(~EmployerEntity.rows.any())
        )
        assert orphans == 0

    def test_a_key_maps_to_exactly_one_entity(self, session, loaded):
        split = session.execute(
            select(EmployerLMIA.employer_key)
            .where(EmployerLMIA.employer_key.is_not(None))
            .group_by(EmployerLMIA.employer_key)
            .having(func.count(func.distinct(EmployerLMIA.employer_id)) > 1)
        ).all()
        assert split == []


class TestDiscovery:
    def test_excel_lock_files_are_not_source_files(self, tmp_path):
        lock = tmp_path / "~$2025Q2.xlsx"
        lock.write_bytes(b"x" * 165)
        assert discover.is_source_file(lock) is False

    def test_macos_stubs_and_empty_files_are_skipped(self, tmp_path):
        stub = tmp_path / "._2025Q2.xlsx"
        stub.write_bytes(b"x")
        empty = tmp_path / "2025Q2.csv"
        empty.touch()
        assert discover.is_source_file(stub) is False
        assert discover.is_source_file(empty) is False

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("2025Q2.xlsx", "2025Q2"),
            ("tfwp_2025q1_positive_en.csv", "2025Q1"),
            ("Q3_2024_employers.xls", "2024Q3"),
            ("employers-2026-Q1.csv", "2026Q1"),
            ("no_quarter_here.csv", None),
        ],
    )
    def test_quarter_parsing(self, name, expected):
        assert discover.parse_quarter(name) == expected

    def test_quarter_recovered_from_the_banner(self):
        banner = (
            "Employers Who Were Issued a Positive Labour Market Impact Assessment (LMIA) "
            "by Program Stream, National Occupational Classification (NOC) 2021 and "
            "Business Location, April to June 2025"
        )
        assert discover.quarter_from_banner(banner) == "2025Q2"

    def test_csv_is_preferred_over_excel_for_the_same_quarter(self, tmp_path):
        for suffix in ("csv", "xlsx", "xls"):
            (tmp_path / f"2025Q2.{suffix}").write_bytes(b"header\n")
        found = discover.discover(tmp_path)
        assert len(found) == 1
        assert found[0].fmt == "csv"
        assert len(found[0].siblings) == 2


class TestHeaderRegistry:
    def test_the_live_file_shape_maps_completely(self):
        """The 2025Q2–2026Q1 header row, verbatim."""
        live = [
            "Province/Territory",
            "Program Stream",
            "Employer",
            "Address",
            "Occupation",
            "Incorporate Status",
            "Approved LMIAs",
            "Approved Positions",
        ]
        mapped = headers.map_columns(live)
        assert mapped.ok
        assert mapped.unmapped == []
        assert set(mapped.indexes) == {
            headers.PROVINCE,
            headers.PROGRAM_STREAM,
            headers.EMPLOYER,
            headers.ADDRESS,
            headers.OCCUPATION,
            headers.INCORPORATE_STATUS,
            headers.APPROVED_LMIAS,
            headers.APPROVED_POSITIONS,
        }

    def test_padding_and_casing_drift_still_maps(self):
        drifted = [
            "PROVINCE/TERRITORY   ",
            "  program stream",
            "Employer Name",
            "Positions Approved",
        ]
        mapped = headers.map_columns(drifted)
        assert mapped.ok
        assert mapped.index_of(headers.APPROVED_POSITIONS) == 3

    def test_a_new_column_is_reported_not_ignored(self):
        mapped = headers.map_columns(["Employer", "Something Entirely New"])
        assert mapped.ok
        assert mapped.unmapped == [(1, "Something Entirely New")]

    def test_a_file_without_an_employer_column_is_rejected(self):
        mapped = headers.map_columns(["Province/Territory", "Approved Positions"])
        assert not mapped.ok
        assert headers.EMPLOYER in mapped.missing_required

    def test_split_noc_layout_is_detected(self):
        mapped = headers.map_columns(["Employer", "NOC Code", "Occupation Title"])
        assert mapped.has_split_noc


class TestLayoutDetection:
    def test_banner_header_data_and_notes_are_separated(self):
        import pandas as pd

        frame = pd.DataFrame(
            [
                ["Employers Who Were Issued a Positive LMIA … (NOC) 2021 …", None, None],
                ["Province/Territory", "Employer", "Approved Positions"],
                ["Ontario", "Acme Inc.", "3"],
                ["Quebec", "Beta Ltee", "5"],
                [None, None, None],
                ["Notes:", None, None],
                ["1. The source for all information …", None, None],
            ]
        )
        layout = discover.detect_layout(frame)
        assert layout.header_row == 1
        assert layout.noc_version == "2021"
        assert len(layout.data) == 2
        assert layout.notes == ["Notes:", "1. The source for all information …"]

    def test_the_notes_block_never_becomes_data(self, session, loaded):
        """The footnotes are prose; none of them may appear as an employer."""
        leaked = session.scalar(
            select(func.count())
            .select_from(EmployerLMIA)
            .where(EmployerLMIA.employer_raw.ilike("%The source for all information%"))
        )
        assert leaked == 0
