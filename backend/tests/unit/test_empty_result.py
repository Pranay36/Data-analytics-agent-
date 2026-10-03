from app.graph.nodes.sql import is_empty_result


def test_no_rows_is_empty() -> None:
    assert is_empty_result([])


def test_a_single_null_aggregate_is_empty() -> None:
    assert is_empty_result([[None]])


def test_several_null_columns_are_empty() -> None:
    assert is_empty_result([[None, None]])


def test_a_zero_is_data_not_emptiness() -> None:
    """Zero revenue is a real answer; only NULL means "nothing matched"."""
    assert not is_empty_result([[0]])
    assert not is_empty_result([[0.0]])


def test_any_real_value_makes_it_non_empty() -> None:
    assert not is_empty_result([[None, 5]])
    assert not is_empty_result([[None], [3]])
