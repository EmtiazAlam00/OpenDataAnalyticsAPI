"""Endpoint behaviour.

Weighted toward the things that would be wrong in a way nobody notices: a
filter that silently does nothing, a total that disagrees with the rows it
summarises, a caveat that stops being served.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestMeta:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body == {"status": "ok", "database": "ok"}

    def test_attribution_is_served_verbatim(self, client):
        body = client.get("/attribution").json()
        assert body["statement"] == (
            "Contains information licensed under the Open Government Licence – Canada."
        )

    def test_about_carries_every_caveat(self, client):
        caveats = {c["id"] for c in client.get("/about").json()["caveats"]}
        assert caveats == {
            "incomplete-by-construction",
            "positions-not-workers",
            "noc-version-drift",
            "pr-only-break",
        }

    def test_quarters_report_comparability_not_just_counts(self, client):
        quarters = client.get("/quarters").json()
        assert quarters
        for q in quarters:
            assert q["noc_version"] is not None
            assert isinstance(q["includes_pr_only"], bool)
            assert q["rows"] > 0

    def test_ingest_report_exposes_the_tallies(self, client):
        report = client.get("/meta/ingest").json()
        assert report["files"]
        for f in report["files"]:
            assert f["balances"] is True, f"{f['source_file']} tallies do not add up"


class TestDashboard:
    def test_dash_is_served(self, client):
        response = client.get("/dash")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_dash_reads_only_public_endpoints(self, client):
        """The page must not depend on anything the API does not also expose.

        It fetches these by name, so a rename that forgot the dashboard would
        leave it silently empty in the browser.
        """
        body = client.get("/dash").text
        for path in ("/quarters", "/trends/positions", "/stats/top-employers",
                     "/stats/by-province", "/meta/ingest", "/about"):
            assert f'"{path}"' in body, f"dashboard does not reference {path}"
            assert client.get(path).status_code == 200

    def test_dash_is_hidden_from_the_schema(self, client):
        """It is a page, not an API operation."""
        assert "/dash" not in client.get("/openapi.json").json()["paths"]


class TestEmployers:
    def test_pagination_is_consistent(self, client):
        body = client.get("/employers", params={"page_size": 5}).json()
        assert len(body["items"]) == 5
        assert body["pages"] == -(-body["total"] // 5)

    def test_page_size_is_capped(self, client):
        assert client.get("/employers", params={"page_size": 10_000}).status_code == 422

    def test_unknown_row_is_404_not_an_empty_object(self, client):
        assert client.get("/employers/999999999").status_code == 404

    def test_province_filter_actually_filters(self, client):
        body = client.get("/employers", params={"province": "ON", "page_size": 20}).json()
        assert body["total"] > 0
        assert {i["province"] for i in body["items"]} == {"ON"}

    def test_quarter_filter_rejects_a_malformed_quarter(self, client):
        assert client.get("/employers", params={"quarter": "2025-Q2"}).status_code == 422

    def test_search_matches_the_trade_name_as_well_as_the_legal_name(self, client):
        body = client.get("/employers", params={"q": "Tim Hortons", "page_size": 50}).json()
        assert body["total"] > 0
        # Rows where the brand appears only after 'o/a' must still be found.
        assert any(
            "tim hortons" not in (i["employer"] or "").lower()
            or "tim hortons" in (i["trade_name"] or "").lower()
            for i in body["items"]
        )

    def test_pr_only_filter_is_symmetric(self, client):
        total = client.get("/employers", params={"page_size": 1}).json()["total"]
        yes = client.get("/employers", params={"pr_only": True, "page_size": 1}).json()["total"]
        no = client.get("/employers", params={"pr_only": False, "page_size": 1}).json()["total"]
        assert yes + no == total


class TestEntities:
    def test_an_entity_exposes_the_variants_merged_into_it(self, client):
        merged = client.get("/entities", params={"min_variants": 2, "page_size": 1}).json()
        if not merged["items"]:
            pytest.skip("no multi-variant entities in the loaded data")
        entity = client.get(f"/entities/{merged['items'][0]['id']}").json()
        assert len(entity["variants"]) == entity["variant_count"] >= 2
        assert entity["quarters"]

    def test_grouping_is_labelled_with_its_method(self, client):
        body = client.get("/entities", params={"page_size": 5}).json()
        assert {i["match_method"] for i in body["items"]} == {"deterministic"}

    def test_unknown_entity_is_404(self, client):
        assert client.get("/entities/999999999").status_code == 404


class TestStats:
    def test_top_employers_is_ranked_and_capped(self, client):
        rows = client.get("/stats/top-employers", params={"limit": 10}).json()
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
        positions = [r["approved_positions"] for r in rows]
        assert positions == sorted(positions, reverse=True)

    @pytest.mark.parametrize("group_by", ["entity", "brand", "raw"])
    def test_every_grouping_mode_works(self, client, group_by):
        rows = client.get("/stats/top-employers", params={"group_by": group_by, "limit": 5}).json()
        assert len(rows) == 5
        assert all(r["name"] for r in rows)

    def test_brand_grouping_consolidates_franchisees(self, client):
        """Franchisees of one brand are separate legal employers but one brand."""
        by_entity = client.get(
            "/stats/top-employers", params={"group_by": "entity", "limit": 200}
        ).json()
        by_brand = client.get(
            "/stats/top-employers", params={"group_by": "brand", "limit": 200}
        ).json()
        # Brand grouping can only ever merge, so its top group is at least as
        # large as the entity view's.
        assert by_brand[0]["approved_positions"] >= by_entity[0]["approved_positions"]

    def test_by_province_can_exclude_the_non_province_bucket(self, client):
        with_bucket = client.get(
            "/stats/by-province", params={"include_non_provinces": True}
        ).json()
        without = client.get("/stats/by-province", params={"include_non_provinces": False}).json()
        assert all(r["province"] is not None for r in without)
        assert len(with_bucket) >= len(without)

    def test_province_totals_match_the_row_endpoint(self, client):
        stats = client.get("/stats/by-province", params={"quarter": "2025Q2"}).json()
        ontario = next(r for r in stats if r["province"] == "ON")
        rows = client.get(
            "/employers", params={"province": "ON", "quarter": "2025Q2", "page_size": 1}
        ).json()
        assert ontario["rows"] == rows["total"]

    def test_by_occupation_always_reports_the_noc_vintage(self, client):
        rows = client.get("/stats/by-occupation", params={"limit": 10}).json()
        assert rows
        assert all(r["noc_version"] for r in rows)


class TestTrends:
    def test_series_is_chronological(self, client):
        body = client.get("/trends/positions").json()
        quarters = [p["quarter"] for p in body["series"]]
        assert quarters == sorted(quarters)

    def test_inverted_range_is_rejected(self, client):
        response = client.get("/trends/positions", params={"from": "2026Q1", "to": "2025Q1"})
        assert response.status_code == 422

    def test_a_range_inside_one_regime_is_marked_comparable(self, client):
        body = client.get("/trends/positions", params={"from": "2025Q2", "to": "2026Q1"}).json()
        assert body["series_breaks"] == []
        assert body["comparable"] is True

    def test_a_range_spanning_the_2023q4_change_is_flagged(self, client):
        """The endpoint must not hand back a series that silently changes basis."""
        body = client.get("/trends/positions", params={"from": "2023Q1", "to": "2026Q1"}).json()
        if not body["series"] or body["series"][0]["quarter"] >= "2023Q4":
            pytest.skip("no pre-2023Q4 quarters loaded")
        assert body["series_breaks"], "a break spanning 2023Q4 was not reported"
        assert body["series_breaks"][0]["quarter"] == "2023Q4"
        assert body["comparable"] is False

    def test_excluding_pr_only_restores_comparability(self, client):
        body = client.get(
            "/trends/positions",
            params={"from": "2023Q1", "to": "2026Q1", "exclude_pr_only": True},
        ).json()
        assert body["comparable"] is True
        assert all(p["pr_only_positions"] == 0 for p in body["series"])

    def test_totals_agree_with_the_quarters_endpoint(self, client):
        trends = {
            p["quarter"]: p["approved_positions"]
            for p in client.get("/trends/positions").json()["series"]
        }
        for q in client.get("/quarters").json():
            assert trends[q["quarter"]] == q["approved_positions"]
