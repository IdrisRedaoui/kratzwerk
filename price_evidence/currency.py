"""Currency codes explicitly recognized by the current evidence extractors."""

from __future__ import annotations

import re
from typing import Any


# Keep this dependency-free and deliberately limited to codes the extractors
# recognize from source evidence today. Expanding it requires an explicit review.
RECOGNIZED_CURRENCY_CODES = frozenset({"DZD", "EUR", "USD"})


def normalize_currency_code(value: Any) -> str | None:
    """Return a normalized recognized code; three-letter shape alone is insufficient."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not re.fullmatch(r"[A-Za-z]{3}", candidate):
        return None
    normalized = candidate.upper()
    return normalized if normalized in RECOGNIZED_CURRENCY_CODES else None


__all__ = ["RECOGNIZED_CURRENCY_CODES", "normalize_currency_code"]
