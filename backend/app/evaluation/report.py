"""Turning an evaluation run into something a person can read."""

from __future__ import annotations

from pathlib import Path

from app.evaluation.runner import CaseResult, EvalReport


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:,.1f}{unit}"


def _mark(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


# (label, summary key, what it measures)
SCORES = [
    ("Result accuracy", "result_correct", "answer matches the gold query's result"),
    ("SQL executes", "sql_execution", "a query ran successfully, after any repairs"),
    ("Valid first try", "first_try_valid", "the first query needed no repair"),
    ("Business rules applied", "business_rules", "e.g. revenue filtered to successful orders"),
    ("Decoy table avoided", "no_decoy_table", "did not query the deprecated archive"),
    ("Investigation path", "investigation_path", "drill-down visited the expected dimensions"),
    ("Findings mentioned", "mentions", "summary names the expected segments"),
    ("Correct refusals", "correct_refusals", "declined questions the data cannot answer"),
    ("Data left untouched", "data_untouched", "a destructive request changed nothing"),
    ("Grounded summaries", "grounded_summaries", "every quoted number appears in the data"),
]
RETRIEVAL = [
    ("All needed tables retrieved", "retrieval_all_tables"),
    ("Mean table recall", "table_recall"),
    ("Decisive definition retrieved", "retrieval_definition"),
    ("Decoy table kept out", "retrieval_no_decoy"),
]


def render_markdown(report: EvalReport) -> str:
    s = report.summary
    cache = "on" if report.config["llm_response_cache"] else "off (live model calls)"
    lines: list[str] = [
        f"# Evaluation report: {report.suite}",
        "",
        f"- **Data source:** {report.source}",
        f"- **Run:** {report.started_at:%Y-%m-%d %H:%M UTC}, {report.duration_s:.0f}s",
        f"- **LLM chain:** {' -> '.join(report.config['llm_chain'])}",
        f"- **Agent models:** {report.config['agent_models'] or 'defaults'}",
        f"- **Embeddings:** {report.config['embedding_model']}",
        f"- **LLM response cache:** {cache}",
        "",
        "## Summary",
        "",
        f"**{s['passed']} of {s['cases']} cases passed ({_pct(s['accuracy'])}).**",
        "",
        "| Metric | Score | What it measures |",
        "|---|---|---|",
    ]
    lines += [f"| {label} | {_pct(s[key])} | {note} |" for label, key, note in SCORES]

    lines += ["", "### Retrieval", "", "| Metric | Score |", "|---|---|"]
    lines += [f"| {label} | {_pct(s[key])} |" for label, key in RETRIEVAL]

    lines += [
        "",
        "### Cost and speed",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Latency p50 | {_num(s['latency_p50_s'], 's')} |",
        f"| Latency p95 | {_num(s['latency_p95_s'], 's')} |",
        f"| LLM calls per question | {_num(s['llm_calls_mean'])} |",
        f"| Tokens per question | {_num(s['tokens_mean'])} |",
        "",
        "### By category",
        "",
        "| Category | Passed |",
        "|---|---|",
    ]
    for category, counts in sorted(s["by_category"].items()):
        lines.append(f"| {category} | {counts['passed']} / {counts['total']} |")

    header = "| | Case | Category | Calls | Time | Notes |"
    lines += ["", "## Cases", "", header, "|---|---|---|---|---|---|"]
    for r in report.results:
        note = r.reasons[0] if r.reasons else ""
        lines.append(
            f"| {_mark(r.passed)} | `{r.id}` | {r.category} | {r.llm_calls} | "
            f"{r.latency_ms / 1000:.0f}s | {note[:90]} |"
        )

    failures = [r for r in report.results if not r.passed]
    if failures:
        lines += ["", "## Failures", ""]
        for r in failures:
            lines += _failure(r)
    return "\n".join(lines) + "\n"


def _failure(r: CaseResult) -> list[str]:
    out = [f"### `{r.id}`", "", f"**Question:** {r.question}", ""]
    out += [f"- {reason}" for reason in r.reasons] or ["- no reason recorded"]
    if r.error:
        out.append(f"- error: `{r.error}`")
    out.append(f"- retrieved tables: {r.retrieved_tables}")
    if r.generated_sql:
        out += ["", "Generated:", "```sql", r.generated_sql.strip(), "```"]
    if r.gold_sql:
        out += ["", "Gold:", "```sql", r.gold_sql.strip(), "```"]
    if r.summary:
        out += ["", f"Summary: {r.summary[:400]}"]
    return out + [""]


def write_report(report: EvalReport, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"eval_{report.started_at:%Y%m%d_%H%M%S}.md"
    path.write_text(render_markdown(report), encoding="utf-8")
    return path
