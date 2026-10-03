"""Checks on how an answer was reached."""

from types import SimpleNamespace

from app.evaluation.checks import (
    check_grounding,
    check_rules,
    extract_numbers,
    tables_in,
)

STATUS = [{"kind": "filter_equals", "column": "status", "value": "SUCCESS"}]


def passed(sql, rules):
    return all(r.ok for r in check_rules(sql, rules))


# ── Rules on the syntax tree ─────────────────────────────────────────────────
def test_the_status_filter_is_found_in_a_where_clause() -> None:
    assert passed("SELECT SUM(x) FROM orders WHERE status = 'SUCCESS'", STATUS)


def test_the_filter_is_found_inside_a_filter_clause() -> None:
    assert passed("SELECT SUM(x) FILTER (WHERE status = 'SUCCESS') FROM orders", STATUS)


def test_the_filter_is_found_with_a_table_alias_and_swapped_sides() -> None:
    assert passed("SELECT 1 FROM orders o WHERE 'SUCCESS' = o.status", STATUS)


def test_the_filter_is_found_in_an_in_list() -> None:
    assert passed("SELECT 1 FROM orders WHERE status IN ('SUCCESS')", STATUS)


def test_a_missing_filter_fails() -> None:
    """The headline trap: revenue summed over every order."""
    assert not passed("SELECT SUM(total_amount) FROM orders", STATUS)


def test_the_wrong_value_does_not_satisfy_the_rule() -> None:
    assert not passed("SELECT 1 FROM orders WHERE status = 'completed'", STATUS)


def test_the_filter_is_found_inside_a_cte() -> None:
    sql = "WITH r AS (SELECT total_amount FROM orders WHERE status = 'SUCCESS') SELECT SUM(total_amount) FROM r"
    assert passed(sql, STATUS)


def test_a_column_named_in_a_string_is_not_a_filter() -> None:
    """A substring match on text would be fooled by this; the tree is not."""
    assert not passed("SELECT 'status = SUCCESS' AS note FROM orders", STATUS)


def test_uses_column_resolves_aliases() -> None:
    rule = [{"kind": "uses_column", "table": "orders", "column": "shipping_region"}]
    assert passed("SELECT o.shipping_region FROM orders o", rule)
    assert not passed("SELECT c.region FROM customers c", rule)


def test_the_wrong_regions_column_is_caught_through_an_alias() -> None:
    """customers.region is the home region and is not used for sales reporting."""
    rule = [{"kind": "forbid_column", "table": "customers", "column": "region"}]
    assert not passed("SELECT c.region, SUM(o.total_amount) FROM orders o JOIN customers c ON c.id=o.customer_id GROUP BY 1", rule)
    assert passed("SELECT o.shipping_region FROM orders o JOIN customers c ON c.id=o.customer_id", rule)


def test_unparseable_sql_fails_every_rule() -> None:
    assert not passed("SELEKT nonsense", STATUS)


def test_tables_are_extracted_without_cte_names() -> None:
    sql = "WITH recent AS (SELECT id FROM orders) SELECT r.id FROM recent r JOIN customers c ON c.id = r.id"
    assert tables_in(sql) == {"orders", "customers"}


def test_the_decoy_archive_table_is_detectable() -> None:
    assert "orders_legacy" in tables_in("SELECT SUM(total_amount) FROM orders_legacy")


# ── Groundedness ─────────────────────────────────────────────────────────────
def query(rows=None, profile=None):
    return SimpleNamespace(status="succeeded", rows=rows or [], profile=profile)


def test_numbers_are_extracted_in_their_common_forms() -> None:
    values = dict(extract_numbers("Revenue fell 11.4% to 19,191,285.85, or 19.19M."))
    assert 11.4 in values.values()
    assert 19191285.85 in values.values()
    assert any(abs(v - 19_190_000) < 1 for v in values.values())


def test_prose_numbers_and_years_are_not_treated_as_claims() -> None:
    assert extract_numbers("In June 2026 it took 3 levels across the top 5 regions.") == []


def test_a_figure_from_the_data_is_grounded() -> None:
    result = check_grounding("Revenue was 19,191,285.85.", [query(rows=[[19191285.85]])])
    assert result.ok and result.checked == 1


def test_a_percentage_from_the_statistics_is_grounded() -> None:
    profile = {"comparison": {"total_pct_change": -11.42,
                              "segments": [{"share_of_total_change": 0.848}]}}
    result = check_grounding("Revenue fell 11.4%, and South explains 85% of it.",
                             [query(profile=profile)])
    assert result.ok, result.ungrounded


def test_a_scaled_figure_is_grounded() -> None:
    assert check_grounding("About 19.2M.", [query(rows=[[19191285.85]])]).ok


def test_an_invented_figure_is_caught() -> None:
    """The failure a reader cannot spot: a plausible number that is not in the data."""
    result = check_grounding("Revenue was 23,500,000.", [query(rows=[[19191285.85]])])
    assert not result.ok and result.ungrounded == ["23,500,000"]


def test_a_decline_quoted_without_its_sign_is_still_grounded() -> None:
    result = check_grounding("It fell by 2,474,434.65.", [query(rows=[[-2474434.65]])])
    assert result.ok


def test_failed_queries_do_not_count_as_evidence() -> None:
    failed = SimpleNamespace(status="failed", rows=[[999999.0]], profile=None)
    assert not check_grounding("Revenue was 999,999.00.", [failed]).ok


def test_tolerance_allows_rounding_but_not_a_different_number() -> None:
    assert check_grounding("About 19,191,286.", [query(rows=[[19191285.85]])]).ok
    assert not check_grounding("About 19,500,000.", [query(rows=[[19191285.85]])]).ok
