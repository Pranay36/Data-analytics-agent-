"""Turn a rejection into something the model can act on.

"Unknown column `orders.region`" leaves the model guessing. "Unknown column
`orders.region`. Did you mean `shipping_region`?" usually gets it right on the
retry — which is the difference between a repair loop that converges and one
that burns its attempts (PROJECT_PLAN §13.4).
"""

from __future__ import annotations

from collections.abc import Iterable
from difflib import get_close_matches


def did_you_mean(name: str, candidates: Iterable[str], *, limit: int = 3) -> str:
    """Return a ' Did you mean ...?' fragment, or '' when nothing is close."""
    options = list(candidates)
    if not options:
        return ""

    lookup = {option.lower(): option for option in options}
    matches = get_close_matches(name.lower(), lookup.keys(), n=limit, cutoff=0.6)

    # Substring matches are often better than edit distance for identifiers:
    # "region" vs "shipping_region" scores poorly but is exactly the right hint.
    if not matches:
        needle = name.lower()
        matches = [key for key in lookup if needle in key or key in needle][:limit]

    if not matches:
        return ""

    formatted = ", ".join(f"`{lookup[match]}`" for match in matches)
    return f" Did you mean {formatted}?"
