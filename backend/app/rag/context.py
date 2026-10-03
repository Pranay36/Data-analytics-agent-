"""Rendering retrieved context into the text an agent actually reads.

Ordering is a deliberate choice. Definitions come before tables because models
anchor on what they read first, and definitions are what prevent SQL that runs
correctly while computing the wrong thing. Examples come last, closest to the
question, because that is the pattern we most want copied.
"""

from __future__ import annotations

from app.rag.retriever import RetrievedContext

# Keeps the prompt affordable on free models, which often have modest context
# and always have rationed throughput.
MAX_CONTEXT_CHARS = 14_000


def render_context(context: RetrievedContext, *, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    sections: list[str] = []

    if context.definitions:
        lines = ["## BUSINESS DEFINITIONS", "Apply these exactly. They override any assumption."]
        lines += [chunk.content for chunk in context.definitions]
        sections.append("\n\n".join(lines))

    if context.tables:
        lines = ["## TABLES", "Use only these tables and columns."]
        lines += [chunk.content for chunk in context.tables]
        sections.append("\n\n".join(lines))

    if context.joins:
        sections.append(
            "## HOW THESE TABLES JOIN\n" + "\n".join(join.render() for join in context.joins)
        )

    if context.examples:
        lines = [
            "## VERIFIED EXAMPLE QUERIES",
            "Proven against this database. Adapt, do not copy blindly.",
        ]
        for chunk in context.examples:
            question = chunk.payload.get("question", chunk.title)
            sql = (chunk.payload.get("sql") or "").strip()
            lines.append(f"Q: {question}\n{sql}")
        sections.append("\n\n".join(lines))

    if context.confidence == "low":
        sections.insert(
            0,
            "## WARNING\nNo table closely matched this question. The tables below may be "
            "irrelevant. If they cannot answer it, say so rather than guessing.",
        )

    rendered = "\n\n".join(sections)
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars].rsplit("\n", 1)[0] + "\n...(context truncated)"
    return rendered
