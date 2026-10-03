"""Defects found by running the evaluation suite against a live model."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.agents.analysis_agent import period_length_warning
from app.agents.schemas import AnalysisAgentOutput, AnalysisFrame, DrillDownProposal, Period

PROPOSAL = DrillDownProposal(
    dimension="products.category", focus_value="South",
    step_question="By category within South?", rationale="South dominates.",
)


# ── A reply whose flag contradicts its own proposal ──────────────────────────
def test_a_proposal_with_the_flag_off_is_treated_as_a_request_to_drill() -> None:
    """Seen live: a complete, valid next step with a rationale, and needs_drilldown
    false. A three-level investigation stopped at one because the flag was obeyed over
    the proposal. The guard still validates whatever is proposed, so honouring it is safe."""
    output = AnalysisAgentOutput(summary="South fell.", needs_drilldown=False, drilldown=PROPOSAL)
    assert output.needs_drilldown is True


def test_no_proposal_and_no_flag_means_stop() -> None:
    output = AnalysisAgentOutput(summary="Nothing more to find.", needs_drilldown=False)
    assert output.needs_drilldown is False and output.drilldown is None


def test_the_flag_without_a_proposal_is_still_rejected() -> None:
    with pytest.raises(ValidationError, match="drilldown is required"):
        AnalysisAgentOutput(summary="x", needs_drilldown=True)


# ── Periods of unequal length ────────────────────────────────────────────────
def frame(previous: tuple[date, date], current: tuple[date, date]) -> AnalysisFrame:
    return AnalysisFrame(
        metric_name="Refunds", metric_sql="SUM(amount)", base_table="refunds",
        comparison_period=Period(start=previous[0], end=previous[1], label="Jan-Apr 2026"),
        current_period=Period(start=current[0], end=current[1], label="May-Jun 2026"),
    )


def test_four_months_against_two_is_flagged() -> None:
    """The failure: four months of refund totals were compared with two, and the system
    concluded refunds had fallen 12.6%, telling the user their premise was wrong."""
    warning = period_length_warning(
        frame((date(2026, 1, 1), date(2026, 5, 1)), (date(2026, 5, 1), date(2026, 7, 1)))
    )
    assert warning is not None
    assert "120 days" in warning and "61 days" in warning
    assert "not directly comparable" in warning


def test_one_month_against_another_is_not_flagged() -> None:
    """May has 31 days and June 30; that is not a meaningful difference."""
    assert period_length_warning(
        frame((date(2026, 5, 1), date(2026, 6, 1)), (date(2026, 6, 1), date(2026, 7, 1)))
    ) is None


def test_february_against_march_is_not_flagged() -> None:
    assert period_length_warning(
        frame((date(2026, 2, 1), date(2026, 3, 1)), (date(2026, 3, 1), date(2026, 4, 1)))
    ) is None


def test_a_missing_period_gives_no_warning() -> None:
    assert period_length_warning(None) is None
    partial = AnalysisFrame(metric_name="x", metric_sql="x", base_table="t")
    assert period_length_warning(partial) is None


# ── A comparison without a usable frame cannot be drilled ────────────────────
from app.agents.schemas import QueryAgentOutput  # noqa: E402

PERIODS = {
    "current_period": {"start": "2026-05-01", "end": "2026-07-01", "label": "May-Jun"},
    "comparison_period": {"start": "2026-01-01", "end": "2026-05-01", "label": "Jan-Apr"},
}


def out(**fields):
    base = {"can_answer": True, "sql": "SELECT 1", "explanation": "x"}
    return QueryAgentOutput(**{**base, **fields})


def test_a_comparison_without_a_frame_is_rejected_so_it_is_repaired() -> None:
    with pytest.raises(ValidationError, match="frame is required"):
        out(question_type="comparison")


def test_a_root_cause_question_with_a_frame_lacking_periods_is_rejected() -> None:
    """The case seen live: a frame was returned, but with no periods in it."""
    bare = {"metric_name": "Refunds", "metric_sql": "SUM(amount)", "base_table": "refunds"}
    with pytest.raises(ValidationError, match="both current_period and comparison_period"):
        out(question_type="root_cause", frame=bare)


def test_a_complete_frame_is_accepted() -> None:
    frame = {"metric_name": "Refunds", "metric_sql": "SUM(amount)", "base_table": "refunds",
             **PERIODS}
    assert out(question_type="comparison", frame=frame).frame.current_period.label == "May-Jun"


def test_a_useless_frame_on_a_simple_question_is_discarded_not_rejected() -> None:
    """A metric question never needed a frame; failing it over one would be wrong."""
    bare = {"metric_name": "Revenue", "metric_sql": "SUM(x)", "base_table": "orders"}
    assert out(question_type="metric", frame=bare).frame is None


def test_a_refusal_needs_no_frame() -> None:
    assert out(can_answer=False, sql=None, cannot_answer_reason="no data",
               question_type="comparison").frame is None
