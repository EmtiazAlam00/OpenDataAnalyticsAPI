"""Normalizer unit tests. No database, no files.

Every case here is a real string from the 2025Q2–2026Q1 files or a documented
variant of one. Where a test encodes a judgement call rather than a fact, the
reasoning is in the test name.
"""

from __future__ import annotations

import pytest

from ingest.normalize import address, employer, numbers, occupation, province, stream
from ingest.normalize.text import clean, strip_accents


class TestClean:
    def test_collapses_the_fixed_width_padding_the_export_adds(self):
        assert clean("Ontario                           ") == "Ontario"

    def test_folds_typographic_apostrophes(self):
        # 'St. John's' arrives with U+2019 in some rows and U+0027 in others.
        assert clean("St. John’s") == clean("St. John's")

    @pytest.mark.parametrize("empty", ["", "   ", "nan", "N/A", "-", None])
    def test_empty_markers_become_none(self, empty):
        assert clean(empty) is None

    def test_strip_accents_is_for_keys_not_display(self):
        assert strip_accents("Montréal") == "Montreal"
        assert clean("Montréal") == "Montréal"


class TestProvince:
    def test_padded_variants_reach_one_code(self):
        padded = "Ontario" + " " * 38
        assert province.normalize(padded) == ("ON", province.SCOPE_PROVINCE)
        assert province.normalize("Ontario") == ("ON", province.SCOPE_PROVINCE)

    def test_all_thirteen_provinces_map(self):
        codes = {province.normalize(name)[0] for name in province.NAMES.values()}
        assert codes == set(province.CODES)
        assert len(codes) == 13

    def test_outside_canada_is_not_a_fourteenth_province(self):
        # This value appears in the Province/Territory column but is not a
        # region. It must not show up in a per-province breakdown.
        code, scope = province.normalize(
            "Employers carrying on business in Canada with Head Office outside of Canada"
        )
        assert code is None
        assert scope == province.SCOPE_OUTSIDE_CANADA

    def test_french_names_map_for_the_archive(self):
        assert province.normalize("Québec")[0] == "QC"
        assert province.normalize("Colombie-Britannique")[0] == "BC"

    def test_unrecognised_value_is_distinguishable_from_outside_canada(self):
        assert province.normalize("Atlantis") == (None, province.SCOPE_UNKNOWN)


class TestStream:
    def test_padding_and_case(self):
        assert stream.normalize("    High Wage") == stream.HIGH_WAGE
        assert stream.normalize("High Wage          ") == stream.HIGH_WAGE

    def test_french_label_in_the_english_file(self):
        # 'Talent mondial' appears alongside 'Global Talent Stream'.
        assert stream.normalize("Talent mondial   ") == stream.GLOBAL_TALENT

    def test_pr_only_is_flagged(self):
        canonical = stream.normalize("Permanent Resident Only")
        assert canonical == stream.PR_ONLY
        assert stream.is_pr_only(canonical) is True
        assert stream.is_pr_only(stream.normalize("Low Wage")) is False

    def test_unknown_stream_returns_none_rather_than_raising(self):
        # An unrecognised label must degrade to NULL, not reject the row.
        assert stream.normalize("Some Future Stream") is None


class TestOccupation:
    def test_splits_on_the_first_hyphen_only(self):
        # The title legitimately contains hyphens and commas of its own.
        code, title = occupation.split("72020-Contractors and supervisors, mechanic trades")
        assert code == "72020"
        assert title == "Contractors and supervisors, mechanic trades"

    def test_title_containing_a_hyphen_survives(self):
        code, title = occupation.split("14100-General office support workers - clerical")
        assert code == "14100"
        assert title == "General office support workers - clerical"

    def test_unpacked_value_keeps_its_text(self):
        code, title = occupation.split("Administrative officers")
        assert code is None
        assert title == "Administrative officers"

    def test_banner_version_beats_code_width(self):
        # Footnote 4: ESDC retroactively converted old occupations to NOC 2021,
        # so the file's banner is authoritative even for a 4-digit code.
        assert occupation.infer_version("1221", "2021") == "2021"

    def test_code_width_is_the_fallback_when_the_banner_is_silent(self):
        assert occupation.infer_version("13100", None) == occupation.NOC_2021
        assert occupation.infer_version("1221", None) == occupation.NOC_2011


class TestNumbers:
    def test_plain_integers(self):
        assert numbers.parse_int("5")[0] == 5
        assert numbers.parse_int("1,250")[0] == 1250

    def test_a_range_is_not_collapsed_to_an_endpoint(self):
        # Picking either end would invent precision the publisher did not give.
        parsed, raw, reason = numbers.parse_int("1-5")
        assert parsed is None
        assert raw == "1-5"
        assert reason == "range"

    def test_unparseable_text_is_preserved_with_a_reason(self):
        parsed, raw, reason = numbers.parse_int("suppressed")
        assert parsed is None
        assert raw == "suppressed"
        assert reason == "unparseable"

    def test_blank_is_not_an_anomaly(self):
        assert numbers.parse_int(None) == (None, None, None)


class TestAddress:
    def test_full_address(self):
        parsed = address.parse("Grand Falls-Windsor, NL A2A 1X3")
        assert parsed.city == "Grand Falls-Windsor"
        assert parsed.province_code == "NL"
        assert parsed.postal_code == "A2A 1X3"
        assert parsed.postal_truncated is False

    def test_truncated_postal_code_keeps_the_fsa(self):
        # 1,463 rows across the four quarters are truncated like this.
        parsed = address.parse("St-François, NB E7A  1A")
        assert parsed.postal_fsa == "E7A"
        assert parsed.postal_code is None
        assert parsed.postal_truncated is True

    def test_city_with_an_apostrophe_survives_the_split(self):
        assert address.parse("St. John's, NL A1C 6C9").city == "St. John's"


class TestEmployerKey:
    @pytest.mark.parametrize(
        "variant",
        [
            "Tim Hortons Inc.",
            "TIM HORTON'S",
            "TIM HORTONS #4021",
            "tim hortons limited",
        ],
    )
    def test_spelling_variants_of_one_name_share_a_key(self, variant):
        assert employer.make_key(variant) == employer.make_key("Tim Hortons")

    def test_a_franchisee_is_not_merged_into_its_brand(self):
        # Thirteen separate companies operate Tim Hortons outlets in these
        # files. Collapsing them would report independent employers as one.
        franchisee = employer.make_key("1317518 Alberta Ltd. o/a Tim Hortons")
        assert franchisee != employer.make_key("Tim Hortons")
        assert franchisee == employer.make_key("1317518 Alberta Ltd.")

    def test_brand_key_provides_the_other_view(self):
        assert employer.make_brand_key("1317518 Alberta Ltd. o/a Tim Hortons") == "TIM HORTONS"
        assert employer.make_brand_key("Adams 22 Holdings Ltd. dba Tim Hortons") == "TIM HORTONS"

    def test_brand_key_falls_back_to_the_legal_name(self):
        # An employer with no trade name must still appear in a brand grouping.
        assert employer.make_brand_key("Jealous Fruits Ltd.") == employer.make_key(
            "Jealous Fruits Ltd."
        )

    def test_legal_suffix_stripped_only_at_the_end(self):
        # 'Limited' is noise in 'Seabase Newfoundland Limited' and the business
        # itself in 'Limited Edition Salon'.
        assert employer.make_key("Seabase Newfoundland Limited") == "SEABASE NEWFOUNDLAND"
        assert employer.make_key("Limited Edition Salon") == "LIMITED EDITION SALON"

    def test_ampersand_and_accents_fold(self):
        assert employer.make_key("71516 Newfoundland & Labrador Inc.") == (
            "71516 NEWFOUNDLAND AND LABRADOR"
        )
        assert employer.make_key("Biscuits Leclerc Ltée") == employer.make_key("Biscuits Leclerc")

    def test_two_different_numbered_companies_never_collapse(self):
        assert employer.make_key("1234567 Ontario Inc.") != employer.make_key(
            "7654321 Ontario Inc."
        )

    def test_empty_input_is_unresolvable_rather_than_a_shared_key(self):
        assert employer.make_key(None) is None
        assert employer.make_key("   ") is None

    def test_canonical_name_picks_the_most_frequent_spelling(self):
        variants = ["Tim Hortons", "Tim Hortons", "TIM HORTON'S"]
        assert employer.canonical_name(variants) == "Tim Hortons"

    def test_canonical_name_is_stable_regardless_of_order(self):
        a = employer.canonical_name(["B Corp", "A Corp", "A Corp"])
        b = employer.canonical_name(["A Corp", "A Corp", "B Corp"])
        assert a == b == "A Corp"
