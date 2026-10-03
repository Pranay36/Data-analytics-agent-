"""Measuring how often the system is right, against computed ground truth."""

from app.evaluation.cases import Case, Suite, load_suite
from app.evaluation.compare import compare_results
from app.evaluation.runner import CaseResult, EvalReport, run_retrieval_only, run_suite

__all__ = [
    "Case",
    "CaseResult",
    "EvalReport",
    "Suite",
    "compare_results",
    "load_suite",
    "run_retrieval_only",
    "run_suite",
]
