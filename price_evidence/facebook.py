from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from price_evidence.currency import normalize_currency_code
from price_evidence.models import PriceCandidate


EXTRACTION_METHOD = "facebook_jsonld_product_offer"
EXTRACTION_VERSION = "v1"
_NUMBER_CHARS = re.compile(r"^[0-9][0-9 ,.\u00a0\u202f'’]*[0-9]$|^[0-9]+$")
_STARTING = re.compile(r"\b(starting\s+from|from|à\s+partir\s+de|a\s+partir\s+de)\b", re.I)
_INSTALLMENT = re.compile(r"\b(per\s+month|monthly|installment|par\s+mois|mensuel)\b", re.I)
_RANGE_SPLIT = re.compile(r"\s+(?:-|–|—|to|à)\s+", re.I)


@dataclass(frozen=True)
class LandingPageExtraction:
    extraction_status: str
    candidates: tuple[PriceCandidate, ...]
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]
    method: str = EXTRACTION_METHOD
    version: str = EXTRACTION_VERSION


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._script_type: str | None = None
        self._parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attributes = {key.lower(): value for key, value in attrs}
        if (attributes.get("type") or "").split(";")[0].strip().lower() == "application/ld+json":
            self._script_type = "application/ld+json"
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._script_type:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script_type:
            self.scripts.append("".join(self._parts))
            self._script_type = None
            self._parts = []


def _schema_types(node: Mapping[str, Any]) -> set[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return {str(value).rsplit("/", 1)[-1].lower() for value in values if isinstance(value, str)}


def _walk_nodes(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            if isinstance(child, (Mapping, list)):
                yield from _walk_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_nodes(child)


def _product_key(product: Mapping[str, Any]) -> str:
    for key in ("@id", "sku", "productID", "mpn"):
        value = product.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return f"{key}:{str(value).strip()}"
    canonical = json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "jsonld:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _all_products(nodes: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    products: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        if "product" not in _schema_types(node):
            continue
        key = _product_key(node)
        products.setdefault(key, node)
        variants = node.get("hasVariant")
        for variant in variants if isinstance(variants, list) else [variants]:
            if isinstance(variant, Mapping) and "product" in _schema_types(variant):
                products.setdefault(_product_key(variant), variant)
    return list(products.values())


def _resolve_offer(value: Any, by_id: Mapping[str, Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    values = value if isinstance(value, list) else [value]
    output: list[Mapping[str, Any]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        if set(item) <= {"@id"} and item.get("@id") in by_id:
            output.append(by_id[item["@id"]])
        else:
            output.append(item)
    return output


def _currency(code: Any, raw_text: str | None) -> tuple[str | None, str, str | None]:
    text = raw_text or ""
    text_code: str | None = None
    if re.search(r"(?<![A-Za-z])DZD(?![A-Za-z])|\bDA\b|د\s*ج|د\.ج", text, re.I):
        text_code = "DZD"
    elif re.search(r"(?<![A-Za-z])EUR(?![A-Za-z])|€", text, re.I):
        text_code = "EUR"
    elif re.search(r"(?<![A-Za-z])USD(?![A-Za-z])", text, re.I):
        text_code = "USD"
    elif "$" in text:
        return None, "UNKNOWN", "CURRENCY_SYMBOL_AMBIGUOUS"
    else:
        # Preserve an explicit but unsupported code as contradictory evidence. It
        # must not be overridden by a supported priceCurrency supplied beside it.
        token = re.search(r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])", text)
        if token:
            return None, "UNKNOWN", "CURRENCY_CODE_UNRECOGNIZED"

    structured_code = normalize_currency_code(code)
    if code not in (None, "") and structured_code is None:
        return None, "UNKNOWN", "CURRENCY_CODE_UNRECOGNIZED"
    if structured_code and text_code and structured_code != text_code:
        return None, "UNKNOWN", "CURRENCY_EVIDENCE_CONTRADICTORY"
    if structured_code:
        return structured_code, "SOURCE_CONFIRMED", None
    if text_code:
        return text_code, "SOURCE_CONFIRMED", None
    return None, "UNKNOWN", "CURRENCY_NOT_EXPLICIT"


def _numeric_text(value: Any) -> tuple[float | None, str]:
    if isinstance(value, bool) or value is None:
        return None, "ERROR"
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None, "ERROR"
        return (float(number), "SUCCESS") if number.is_finite() and number >= 0 else (None, "ERROR")
    if not isinstance(value, str):
        return None, "ERROR"
    text = value.strip().replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"^(?:à\s+partir\s+de|a\s+partir\s+de|starting\s+from|from)\s*", "", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z])(?:DZD|EUR|USD|DA)(?![A-Za-z])|د\s*ج|د\.ج|[€$]", "", text, flags=re.I)
    text = text.strip().strip(".,;:")
    if not _NUMBER_CHARS.fullmatch(text):
        return None, "ERROR"

    compact = text.replace(" ", "").replace("'", "").replace("’", "")
    if "," in compact and "." in compact:
        decimal_mark = "," if compact.rfind(",") > compact.rfind(".") else "."
        grouping_mark = "." if decimal_mark == "," else ","
        integer, fraction = compact.rsplit(decimal_mark, 1)
        if len(fraction) not in (1, 2) or not integer:
            return None, "AMBIGUOUS"
        integer = integer.replace(grouping_mark, "")
        normalized = integer + "." + fraction
    elif "," in compact or "." in compact:
        mark = "," if "," in compact else "."
        pieces = compact.split(mark)
        if len(pieces) > 2:
            if all(len(part) == 3 for part in pieces[1:]) and 1 <= len(pieces[0]) <= 3:
                normalized = "".join(pieces)
            else:
                return None, "AMBIGUOUS"
        else:
            integer, fraction = pieces
            if not fraction:
                return None, "ERROR"
            if len(fraction) in (1, 2):
                normalized = integer + "." + fraction
            elif len(fraction) == 3 and 1 <= len(integer) <= 3:
                return None, "AMBIGUOUS"
            elif len(fraction) == 3 and len(integer) > 3:
                normalized = integer + fraction
            else:
                return None, "AMBIGUOUS"
    else:
        normalized = compact
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None, "ERROR"
    if not amount.is_finite() or amount < 0:
        return None, "ERROR"
    return float(amount), "SUCCESS"


def _price_shape(raw: Any, offer: Mapping[str, Any]) -> tuple[float | None, float | None, str, str, list[str]]:
    reasons: list[str] = []
    if raw is None:
        return None, None, "OTHER", "MISSING", ["STRUCTURED_PRICE_MISSING"]
    types = _schema_types(offer)
    note = " ".join(str(offer.get(key, "")) for key in ("description", "name", "priceType"))
    if "aggregateoffer" in types or offer.get("lowPrice") is not None:
        low, low_status = _numeric_text(offer.get("lowPrice"))
        high_raw = offer.get("highPrice")
        high, high_status = _numeric_text(high_raw) if high_raw is not None else (None, "SUCCESS")
        if low_status != "SUCCESS" or high_status != "SUCCESS":
            return None, None, "RANGE", "AMBIGUOUS", ["AGGREGATE_OFFER_AMOUNT_UNPARSEABLE"]
        if high is not None:
            if high < low:
                return None, None, "RANGE", "ERROR", ["AGGREGATE_OFFER_RANGE_REVERSED"]
            return low, high, "RANGE", "SUCCESS", reasons
        if _STARTING.search(note):
            return low, None, "STARTING_FROM", "SUCCESS", reasons
        return low, None, "STARTING_FROM", "SUCCESS", ["AGGREGATE_OFFER_LOW_PRICE_ONLY"]

    if isinstance(raw, str):
        range_parts = _RANGE_SPLIT.split(raw.strip())
        if len(range_parts) == 2:
            first, first_status = _numeric_text(range_parts[0])
            second, second_status = _numeric_text(range_parts[1])
            if first_status == second_status == "SUCCESS" and first is not None and second is not None:
                return min(first, second), max(first, second), "RANGE", "SUCCESS", reasons
        if _STARTING.search(raw):
            amount, status = _numeric_text(raw)
            return amount, None, "STARTING_FROM", status, reasons
        if _INSTALLMENT.search(note + " " + raw):
            amount, status = _numeric_text(raw)
            return amount, None, "INSTALLMENT", status, reasons

    amount, status = _numeric_text(raw)
    type_text = str(offer.get("priceType") or "").lower()
    if "sale" in type_text or "special" in type_text:
        price_type = "SALE"
    elif "regular" in type_text or "listprice" in type_text:
        price_type = "REGULAR"
    elif raw is None:
        price_type = "OTHER"
    else:
        price_type = "REGULAR"
    return amount, None, price_type, status, reasons


def _price_specifications(offer: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], str]]:
    specs = offer.get("priceSpecification")
    values = specs if isinstance(specs, list) else [specs]
    result: list[tuple[Mapping[str, Any], str]] = []
    for spec in values:
        if not isinstance(spec, Mapping):
            continue
        label = " ".join(str(spec.get(key, "")) for key in ("name", "priceType", "@type")).lower()
        if any(word in label for word in ("sale", "special", "promo")):
            result.append((spec, "SALE"))
        elif any(word in label for word in ("regular", "list price", "original")):
            result.append((spec, "REGULAR"))
    return result


def _candidate(
    *,
    source_entity_key: str,
    product: Mapping[str, Any] | None,
    product_key: str | None,
    identity_state: str,
    identity_evidence: Mapping[str, Any] | None,
    raw_value: Any,
    offer: Mapping[str, Any],
    price_type_override: str | None = None,
) -> PriceCandidate:
    amount, amount_max, price_type, parse_status, shape_reasons = _price_shape(raw_value, offer)
    if price_type_override:
        price_type = price_type_override
    currency_code, currency_basis, currency_reason = _currency(
        offer.get("priceCurrency"), raw_value if isinstance(raw_value, str) else None
    )
    reasons = list(shape_reasons)
    if currency_reason:
        reasons.append(currency_reason)
    unit_code = offer.get("unitCode")
    source_unit = str(unit_code) if isinstance(unit_code, (str, int)) else None
    unit_status = "NOT_APPLICABLE" if source_unit is None else "UNRESOLVED"
    if source_unit is not None:
        reasons.append("OFFER_UNIT_CODE_REQUIRES_INTERPRETATION")

    if parse_status == "MISSING":
        extraction_status, quality_status = "MISSING", "MISSING"
    elif parse_status == "ERROR":
        extraction_status, quality_status = "ERROR", "EXTRACTION_ERROR"
        reasons.append("PRICE_AMOUNT_UNPARSEABLE")
    elif parse_status == "AMBIGUOUS":
        extraction_status, quality_status = "AMBIGUOUS", "AMBIGUOUS"
        reasons.append("PRICE_NUMBER_FORMAT_AMBIGUOUS")
    elif identity_state == "AMBIGUOUS":
        extraction_status, quality_status = "SUCCESS", "AMBIGUOUS"
        reasons.append("PRODUCT_IDENTITY_AMBIGUOUS_ON_PAGE")
    elif currency_reason == "CURRENCY_SYMBOL_AMBIGUOUS":
        extraction_status, quality_status = "SUCCESS", "AMBIGUOUS"
    elif currency_code is None or unit_status == "UNRESOLVED":
        extraction_status, quality_status = "SUCCESS", "UNIT_OR_CURRENCY_UNCONFIRMED"
    elif price_type == "OTHER":
        extraction_status, quality_status = "AMBIGUOUS", "AMBIGUOUS"
        reasons.append("PRICE_TYPE_NOT_CLASSIFIED")
    else:
        extraction_status, quality_status = "SUCCESS", "VALID"

    product_name = product.get("name") if isinstance(product, Mapping) else None
    return PriceCandidate(
        source_entity_key=source_entity_key,
        raw_value=raw_value,
        raw_price_text=raw_value if isinstance(raw_value, str) else None,
        candidate_amount=amount,
        candidate_amount_max=amount_max,
        source_unit=source_unit,
        unit_status=unit_status,
        currency_code=currency_code,
        currency_basis=currency_basis,
        price_type=price_type,
        product_identity_state=identity_state,
        extraction_status=extraction_status,
        quality_status=quality_status,
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence={
            "product_key": product_key,
            "product_name": product_name,
            "identity_verification": dict(identity_evidence or {}),
            "product_jsonld": dict(product) if isinstance(product, Mapping) else None,
            "offer_jsonld": dict(offer),
        },
        product_key=product_key,
    )


def _empty_candidate(source_entity_key: str, status: str, reason: str) -> PriceCandidate:
    return PriceCandidate(
        source_entity_key=source_entity_key,
        unit_status="UNKNOWN",
        currency_basis="UNKNOWN",
        price_type="OTHER",
        product_identity_state="UNVERIFIED",
        extraction_status=status,
        quality_status="EXTRACTION_ERROR" if status == "ERROR" else "MISSING",
        reason_codes=(reason,),
        evidence={"source": "jsonld_product_offer"},
    )


def extract_landing_page_prices(
    html: str | None,
    *,
    source_entity_key: str = "unknown-facebook-ad",
    fetch_status: str = "FETCHED",
    verified_product_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> LandingPageExtraction:
    """Extract fixture-supplied JSON-LD offers only; this function performs no I/O."""
    if fetch_status != "FETCHED":
        candidate = _empty_candidate(source_entity_key, "ERROR", "LANDING_PAGE_NOT_ACCESSIBLE")
        return LandingPageExtraction("ERROR", (candidate,), ("LANDING_PAGE_NOT_ACCESSIBLE",), {})
    if not isinstance(html, str) or not html.strip():
        candidate = _empty_candidate(source_entity_key, "ERROR", "PAGE_CONTENT_MISSING")
        return LandingPageExtraction("ERROR", (candidate,), ("PAGE_CONTENT_MISSING",), {})

    parser = _JsonLdParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        candidate = _empty_candidate(source_entity_key, "ERROR", "HTML_PARSE_FAILED")
        return LandingPageExtraction("ERROR", (candidate,), ("HTML_PARSE_FAILED",), {})

    nodes: list[Mapping[str, Any]] = []
    malformed = 0
    for script in parser.scripts:
        try:
            nodes.extend(node for node in _walk_nodes(json.loads(script)) if isinstance(node, Mapping))
        except (json.JSONDecodeError, TypeError):
            malformed += 1

    products = _all_products(nodes)
    if not products:
        status = "ERROR" if malformed and not nodes else "MISSING"
        reason = "MALFORMED_JSONLD" if status == "ERROR" else "NO_PRODUCT_JSONLD_TARGET"
        candidate = _empty_candidate(source_entity_key, status, reason)
        return LandingPageExtraction(
            status,
            (candidate,),
            (reason,),
            {"jsonld_script_count": len(parser.scripts), "malformed_jsonld_count": malformed},
        )

    verified = {
        key: dict(evidence)
        for key, evidence in (verified_product_evidence or {}).items()
        if isinstance(key, str) and isinstance(evidence, Mapping) and evidence
    }
    all_product_keys = {_product_key(product) for product in products}
    multiple_products = len(products) > 1
    by_id = {
        node["@id"]: node
        for node in nodes
        if isinstance(node.get("@id"), str)
    }
    candidates: list[PriceCandidate] = []
    products_without_prices = 0
    for product in products:
        key = _product_key(product)
        variants = product.get("hasVariant")
        is_variant_group = isinstance(variants, list) and len(variants) > 1
        if key in verified:
            identity = "VERIFIED"
        elif multiple_products or is_variant_group:
            identity = "AMBIGUOUS"
        else:
            identity = "UNVERIFIED"

        offers = _resolve_offer(product.get("offers"), by_id)
        generated = 0
        for offer in offers:
            specs = _price_specifications(offer)
            if specs:
                for spec, spec_type in specs:
                    raw = spec.get("price")
                    candidates.append(_candidate(
                        source_entity_key=source_entity_key,
                        product=product,
                        product_key=key,
                        identity_state=identity,
                        identity_evidence=verified.get(key),
                        raw_value=raw,
                        offer={**offer, **spec, "priceCurrency": spec.get("priceCurrency") or offer.get("priceCurrency")},
                        price_type_override=spec_type,
                    ))
                    generated += 1
                continue

            aggregate = "aggregateoffer" in _schema_types(offer) or offer.get("lowPrice") is not None
            raw = offer.get("lowPrice") if aggregate else offer.get("price")
            candidates.append(_candidate(
                source_entity_key=source_entity_key,
                product=product,
                product_key=key,
                identity_state=identity,
                identity_evidence=verified.get(key),
                raw_value=raw,
                offer=offer,
            ))
            generated += 1
        if generated == 0:
            products_without_prices += 1

    if not candidates:
        candidate = _empty_candidate(source_entity_key, "MISSING", "PRODUCT_HAS_NO_STRUCTURED_PRICE")
        candidates.append(candidate)
    reasons = ["MALFORMED_JSONLD_BLOCK_SKIPPED"] if malformed else []
    if multiple_products:
        reasons.append("MULTIPLE_PRODUCT_ENTITIES_ON_PAGE")
    return LandingPageExtraction(
        "SUCCESS" if any(c.candidate_amount is not None for c in candidates) else "MISSING",
        tuple(candidates),
        tuple(reasons),
        {
            "jsonld_script_count": len(parser.scripts),
            "malformed_jsonld_count": malformed,
            "product_entity_count": len(products),
            "product_keys": sorted(all_product_keys),
            "products_without_prices": products_without_prices,
        },
    )

