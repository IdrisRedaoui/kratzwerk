from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from price_evidence.currency import normalize_currency_code
from price_evidence.models import PriceCandidate


EXTRACTION_METHOD = "ouedkniss_frontstore_price"
EXTRACTION_VERSION = "v1"
_SCALE = {
    "UNIT": 1,
    "MILLION": 1_000_000,
    "BILLION": 1_000_000_000,
    "UNIT_PER_SQUARE": 1,
    "MILLION_PER_SQUARE": 1_000_000,
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            result = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        return result if math.isfinite(result) and result >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        result = float(number)
        return result if math.isfinite(result) and result >= 0 else None
    return None


def _currency(value: Any) -> str | None:
    return normalize_currency_code(value)


def assess_ouedkniss_price(
    announcement: Mapping[str, Any],
    *,
    source_entity_key: str = "",
    repeated_one_unit_pattern: bool = False,
    product_identity_state: str = "UNVERIFIED",
) -> PriceCandidate:
    """Parse a source amount without assigning a currency from platform or country."""
    raw_value = announcement.get("price")
    raw_unit_value = announcement.get("priceUnit", announcement.get("price_unit"))
    raw_unit = raw_unit_value if isinstance(raw_unit_value, str) and raw_unit_value else None
    source_unit = raw_unit.upper() if raw_unit else None
    numeric = _number(raw_value)
    reasons: list[str] = []
    source_type = announcement.get("priceType", announcement.get("price_type"))
    price_type = "REGULAR" if source_type == "FIXED" else "OTHER"
    if source_type not in ("FIXED", "OFFERED"):
        reasons.append("PRICE_TYPE_UNRECOGNIZED")
    else:
        # The observed source labels do not establish that the amount is payable.
        reasons.append("PAYABLE_PRICE_NOT_ESTABLISHED_BY_SOURCE_TYPE")

    if raw_value is None:
        extraction_status, quality_status = "MISSING", "MISSING"
        reasons.append("SOURCE_PRICE_MISSING")
        candidate_amount = None
    elif numeric is None:
        extraction_status, quality_status = "ERROR", "EXTRACTION_ERROR"
        reasons.append("SOURCE_PRICE_NOT_A_FINITE_NONNEGATIVE_NUMBER")
        candidate_amount = None
    else:
        extraction_status = "SUCCESS"
        multiplier = _SCALE.get(source_unit or "")
        if multiplier is None:
            candidate_amount = numeric
        else:
            candidate_amount = numeric * multiplier

    if source_unit == "UNIT":
        unit_status = "UNRESOLVED"
        reasons.append("SOURCE_UNIT_UNIT_UNRESOLVED")
    elif source_unit in _SCALE and source_unit not in ("UNIT_PER_SQUARE", "MILLION_PER_SQUARE"):
        unit_status = "CONFIRMED"
    elif source_unit in ("UNIT_PER_SQUARE", "MILLION_PER_SQUARE"):
        unit_status = "UNRESOLVED"
        reasons.append("SOURCE_AREA_UNIT_UNRESOLVED")
    elif source_unit is None:
        unit_status = "UNKNOWN"
        reasons.append("SOURCE_UNIT_MISSING")
    else:
        unit_status = "UNRESOLVED"
        reasons.append("SOURCE_UNIT_UNRECOGNIZED")

    raw_currency = announcement.get("currency")
    currency_code = _currency(raw_currency)
    currency_basis = "SOURCE_CONFIRMED" if currency_code else "UNKNOWN"
    if currency_code is None:
        if raw_currency is None or raw_currency == "":
            reasons.append("CURRENCY_NOT_ESTABLISHED_BY_SOURCE")
        else:
            reasons.append("CURRENCY_CODE_UNRECOGNIZED")

    placeholder_pattern = (
        repeated_one_unit_pattern
        and numeric == 1
        and source_unit == "UNIT"
    )
    if placeholder_pattern:
        quality_status = "SUSPICIOUS_PLACEHOLDER"
        reasons.append("REPEATED_ONE_UNIT_PATTERN_REVIEW")
    elif extraction_status == "SUCCESS":
        if currency_code is None or unit_status in ("UNRESOLVED", "UNKNOWN"):
            quality_status = "UNIT_OR_CURRENCY_UNCONFIRMED"
        elif source_type not in ("FIXED", "OFFERED") or source_type == "OFFERED":
            quality_status = "AMBIGUOUS"
        elif source_type == "FIXED":
            # FIXED is a source label, not by itself proof of a buyer-payable amount.
            quality_status = "AMBIGUOUS"
        else:
            quality_status = "VALID"

    return PriceCandidate(
        source_entity_key=source_entity_key,
        raw_value=raw_value,
        raw_price_text=None if raw_value is None else str(raw_value),
        candidate_amount=candidate_amount,
        source_unit=raw_unit,
        unit_status=unit_status,
        currency_code=currency_code,
        currency_basis=currency_basis,
        price_type=price_type,
        product_identity_state=product_identity_state,
        extraction_status=extraction_status,
        quality_status=quality_status,
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence={
            "source_price": raw_value,
            "source_price_unit": raw_unit_value,
            "source_price_type": source_type,
            "source_currency": raw_currency,
            "candidate_scale_multiplier": (
                _SCALE.get(source_unit or "") if source_unit in _SCALE else None
            ),
            "currency_field_present": "currency" in announcement,
        },
    )

