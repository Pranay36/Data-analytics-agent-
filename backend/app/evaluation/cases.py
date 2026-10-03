"""The shape of an evaluation case, and loading a suite from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DATASETS = Path(__file__).parent / "datasets"

Category = Literal[
    "metric",
    "business_rule",
    "trend",
    "breakdown",
    "join",
    "root_cause",
    "unanswerable",
    "safety",
    "distractor",
    "retrieval_only",
]
Compare = Literal["scalar", "rows_unordered", "rows_ordered", "top_k"]
Outcome = Literal["answer", "cannot_answer", "no_write"]


class PathStep(BaseModel):
    dimension: str
    segment: str | None = None


class Case(BaseModel):
    id: str
    question: str
    category: Category

    # ── What the right answer is ─────────────────────────────────────────────
    gold_sql: str | None = None
    """Run against the live database when the suite runs. The truth is therefore
    computed from the data, never typed in, so it cannot drift from it."""
    compare: Compare | None = None
    top_k: int | None = None
    tolerance: float = 0.005
    """Relative tolerance on numbers, 0.5% by default. Rounding differences between a
    model's SQL and the gold SQL are not errors."""
    allow_percent_scale: bool = False
    """Accept 0.152 for 15.2. A rate is correct whether written as a fraction or a
    percentage, and the question usually does not say which."""

    # ── What else must be true ───────────────────────────────────────────────
    expected_tables: list[str] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    """Business rules the SQL must respect, checked on the syntax tree. See `checks`."""
    forbid_tables: list[str] = Field(default_factory=list)
    decisive_definition: str | None = None
    """A business definition that retrieval must supply for this question."""

    expected_outcome: Outcome = "answer"
    expect_path: list[PathStep] = Field(default_factory=list)
    """For investigations: the dimensions the drill-down should visit, in order."""
    expect_mentions: list[str] = Field(default_factory=list)
    """Words the final summary must contain, for questions whose answer is a finding."""
    note: str = ""

    @model_validator(mode="after")
    def _is_coherent(self) -> Case:
        if self.gold_sql and not self.compare:
            raise ValueError(f"{self.id}: gold_sql needs a compare mode")
        if self.compare == "top_k" and not self.top_k:
            raise ValueError(f"{self.id}: compare 'top_k' needs top_k")
        return self

    @property
    def is_retrieval_only(self) -> bool:
        return self.category == "retrieval_only"


class Suite(BaseModel):
    name: str
    description: str = ""
    cases: list[Case]

    def select(
        self, ids: list[str] | None = None, categories: list[str] | None = None
    ) -> list[Case]:
        chosen = self.cases
        if ids:
            unknown = set(ids) - {case.id for case in chosen}
            if unknown:
                raise ValueError(f"Unknown case id(s): {', '.join(sorted(unknown))}")
            chosen = [case for case in chosen if case.id in ids]
        if categories:
            chosen = [case for case in chosen if case.category in categories]
        return chosen


def load_suite(name: str = "core", path: Path | None = None) -> Suite:
    file = path or DATASETS / f"{name}.yaml"
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    suite = Suite(
        name=data.get("name", name),
        description=data.get("description", ""),
        cases=[Case(**item) for item in data["cases"]],
    )

    ids = [case.id for case in suite.cases]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate case ids in {file.name}: {', '.join(sorted(duplicates))}")
    return suite
