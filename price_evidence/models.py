from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PriceCandidate:
    source_entity_key: str
    raw_value: Any = None
    raw_price_text: str | None = None
    candidate_amount: float | None = None
    candidate_amount_max: float | None = None
    source_unit: str | None = None
    unit_status: str = "UNKNOWN"
    currency_code: str | None = None
    currency_basis: str = "UNKNOWN"
    price_type: str = "OTHER"
    product_identity_state: str = "UNVERIFIED"
    extraction_status: str = "SUCCESS"
    quality_status: str = "AMBIGUOUS"
    reason_codes: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    product_key: str | None = None

