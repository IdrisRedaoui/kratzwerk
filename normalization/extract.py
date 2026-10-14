"""Source-specific text selection, conservative classification, and mentions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import re
import unicodedata
from typing import Any, Mapping

from .text import DEFAULT_PROFILE, NormalizationProfile, normalize_text


class RecordClass(str, Enum):
    PRODUCT = "PRODUCT"
    SERVICE = "SERVICE"
    REAL_ESTATE = "REAL_ESTATE"
    EDUCATION = "EDUCATION"
    UNKNOWN = "UNKNOWN"


CLASSIFICATION_METHOD = "deterministic_rules"


@dataclass(frozen=True)
class Classification:
    value: RecordClass
    rule_id: str
    matched_terms: tuple[str, ...] = ()
    method: str = CLASSIFICATION_METHOD


@dataclass(frozen=True)
class TextSelection:
    field_path: str
    raw_text: str
    explicit_attributes: Mapping[str, str] = field(default_factory=dict)
    source_span_start: int | None = None
    source_span_end: int | None = None


@dataclass(frozen=True)
class ProductMention:
    mention_index: int
    source_span_start: int
    source_span_end: int
    source_text_span: str
    normalized_text: str
    language_code: str | None
    mention_kind: str
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    variant: str | None = None
    quantity_value: float | None = None
    quantity_unit: str | None = None
    capacity_value: float | None = None
    capacity_unit: str | None = None
    specifications_json: str | None = None
    extraction_method: str = "deterministic_rules_v2"
    extraction_confidence: str | None = None


_EDUCATION_TERMS = (
    "school", "summer camp", "camp d'ete", "camp d'été", "ecole", "école",
    "cours", "formation scolaire", "centre de formation", "universite", "université",
)
_REAL_ESTATE_TERMS = (
    "appartement", "apartment", "immobilier", "real estate", "maison", "villa",
    "terrain a vendre", "terrain à vendre", "vente appartement",
)
_SERVICE_TERMS = (
    "location voiture", "location de voiture", "rent a car", "car rental", "rental",
    "vehicle rental", "service", "reparation", "réparation", "diagnostic",
    "mecanique", "mécanique", "emploi", "job", "employment", "opportunite d'emploi",
    "opportunité d'emploi", "offre d'emploi",
)
_ARABIC_SPRAYER_TERMS = (
    "مسدس رش الدهان الكهربائي", "مسدس رش دهان كهربائي",
    "مسدس رش الطلاء الكهربائي", "مسدس رش طلاء كهربائي",
)
_PRODUCT_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("compressor", ("compressor", "compresseur")),
    ("paint", ("peinture", "paint", "acrylique", "acrylic", "soffitto")),
    ("power_tool", (
        "drill", "perceuse", "visseuse", "grinder", "meuleuse",
        *_ARABIC_SPRAYER_TERMS,
    )),
    ("phone", ("smartphone", "telephone", "téléphone", "mobile phone")),
    ("computer", ("laptop", "ordinateur", "computer")),
    ("appliance", ("refrigerateur", "réfrigérateur", "fridge", "aspirateur")),
)
_PRODUCT_TERMS = tuple(
    term for _category, terms in _PRODUCT_CATEGORIES for term in terms
) + ("accessoire", "accessoires", "accessory", "accessories", "kit")
_ACCESSORY_TERMS = ("accessoire", "accessoires", "accessory", "accessories")
_GENERIC_CONTROL_LABELS = {
    "open dropdown", "see ad details", "see summary details", "sponsored",
    "platforms", "categories", "eu transparency", "play video", "play",
    "settings", "enter fullscreen", "unmute", "learn more", "shop now",
    "sign up", "like page", "follow", "send message", "call now", "see more",
    "next items", "previous items", "watch more", "continue watching",
    "continuer à regarder", "read more", "download", "install now", "apply now",
    "book now", "contact us", "get quote",
}
_BRAND_RE = re.compile(
    r"(?i)(?:\bbrand\b|\bmarque\b|\bde\s+marque\b)\s*[:=]?\s*"
    r"([A-Za-zÀ-ÖØ-öø-ÿ\u0600-\u06ff][\w&.-]*)"
)
_MODEL_MARKER_RE = re.compile(
    r"(?i)(?:\bmodel(?:e|èle)?\b|\bmodèle\b|\breference\b|\bréférence\b)"
    r"\s*[:#=]?\s*([A-Za-z0-9][A-Za-z0-9./_-]*)"
)
_IDENTIFIER_RE = re.compile(
    r"(?<![\w])(?=[A-Za-z0-9/-]*[A-Za-z])(?=[A-Za-z0-9/-]*\d)"
    r"[A-Za-z][A-Za-z0-9]*(?:[-/][A-Za-z0-9]+)+(?![\w])"
)
_VARIANT_RE = re.compile(
    r"(?i)(?:\bvariant\b|\bvariante\b|\bcolor\b|\bcouleur\b)"
    r"\s*[:=]?\s*([\wÀ-ÖØ-öø-ÿ-]+)"
)
_CAPACITY_RE = re.compile(
    r"(?i)(?<![\w.])(\d+(?:[.,]\d+)?)\s*(ml|cl|l|litres?|liters?|mah|gb|tb)\b"
)
_OTHER_SPEC_RE = re.compile(
    r"(?i)(?<![\w.])(\d+(?:[.,]\d+)?)\s*"
    r"(v|volt(?:s)?|w|watts?|واط|وات|kg|g|cm|mm|bar|psi)\b"
)
_CROWN_MODEL_RE = re.compile(r"(?i)(?<![\w])CT\d{5}(?![\w])")
_CROWN_CONTEXT_RE = re.compile(r"(?i)(?<![\w])CROWN\s+(CT\d{5})(?![\w])")
_PAINT_CONTEXT_RULES = (
    (
        "non_product_chinese_painting_v2",
        re.compile(r"(?<!\w)peinture\s+traditionnelle\s+chinoise(?!\w)"),
        "peinture traditionnelle chinoise",
    ),
    (
        "non_product_paint_inspiration_v2",
        re.compile(r"(?<!\w)idées?\s+de\s+peinture\b"),
        "idées de peinture",
    ),
    (
        "non_product_ink_painting_v2",
        re.compile(r"(?<!\w)peinture\s+(?:à|a)\s+l'encre(?!\w)"),
        "peinture à l'encre",
    ),
    (
        "non_product_painting_work_sequence_v2",
        re.compile(r"(?<!\w)pon[çc]age\s*,\s*peinture\s*,\s*vernis(?!\w)"),
        "ponçage, peinture, vernis",
    ),
)
_QUANTITY_RE = re.compile(
    r"(?i)(?<!\w)(\d+(?:[.,]\d+)?)\s*(pcs?|pieces?|unit(?:s)?|unites?|unités|packs?)\b"
)
_SPLIT_RE = re.compile(r"\s+\+\s+")
_SPONSORED_KEY = "sponsored"


def classify_record(
    text: str, profile: NormalizationProfile | None = None,
) -> Classification:
    """Apply a fixed, explainable lexical gate; ambiguity defaults away from product."""
    normalized = normalize_text(text, profile or DEFAULT_PROFILE).normalized_text
    for value, rule, terms in (
        (RecordClass.EDUCATION, "education_terms_v1", _EDUCATION_TERMS),
        (RecordClass.REAL_ESTATE, "real_estate_terms_v1", _REAL_ESTATE_TERMS),
        (RecordClass.SERVICE, "service_terms_v1", _SERVICE_TERMS),
    ):
        matches = _matched_terms(normalized, terms)
        if matches:
            return Classification(value, rule, tuple(matches))
    for rule, pattern, evidence in _PAINT_CONTEXT_RULES:
        if pattern.search(normalized):
            return Classification(RecordClass.UNKNOWN, rule, (evidence,))
    if (
        _has_term(normalized, ("peinture",))
        and all(_has_term(normalized, (term,)) for term in (
            "apprenez", "carrière", "compétences", "expression créative",
        ))
    ):
        return Classification(
            RecordClass.UNKNOWN, "non_product_paint_career_copy_v2",
            ("peinture", "apprenez", "carrière", "compétences"),
        )
    matches = _matched_terms(normalized, _PRODUCT_TERMS)
    if matches:
        return Classification(RecordClass.PRODUCT, "explicit_product_terms_v2", tuple(matches))
    return Classification(RecordClass.UNKNOWN, "no_supported_classification_v1")


def select_facebook_text(
    detected_metadata: Mapping[str, Any] | str | None,
) -> TextSelection:
    """Select ad copy from the immutable observation, excluding Facebook UI text.

    Current observations store the card under ``raw_source_payload.card_text``.
    This mirrors Worker 011's post-Sponsored extraction rule; the entity-level
    ``facebook_ads.creative_text`` field is intentionally not consulted.
    """
    metadata = _as_mapping(detected_metadata)
    page_name = metadata.get("page_name")
    if not isinstance(page_name, str):
        nested_metadata = _as_mapping(metadata.get("metadata"))
        page_name = nested_metadata.get("page_name")
    if not isinstance(page_name, str):
        page_name = None
    raw_payload = metadata.get("raw_source_payload")
    raw_payload = _as_mapping(raw_payload)
    card_text = raw_payload.get("card_text")
    if isinstance(card_text, str) and card_text.strip():
        candidate = _creative_line_after_sponsored(card_text, page_name=page_name)
        if candidate is None:
            return TextSelection(
                "facebook_ad_observations.detected_metadata_json.raw_source_payload.card_text#no-copy",
                "",
            )
        raw, start, end = candidate
        return TextSelection(
            "facebook_ad_observations.detected_metadata_json.raw_source_payload.card_text",
            raw, source_span_start=start, source_span_end=end,
        )

    # Some corrected observations contain only the sensor-derived creative field.
    # Accept it only when it is not a recognized control, page name, URL, or UI line.
    fallback = metadata.get("creative_text")
    if isinstance(fallback, str):
        safe = _safe_standalone_creative(fallback, page_name=page_name)
        if safe:
            return TextSelection(
                "facebook_ad_observations.detected_metadata_json.creative_text", safe,
            )
    return TextSelection(
        "facebook_ad_observations.detected_metadata_json.raw_source_payload.card_text#missing",
        "",
    )


def select_ouedkniss_text(
    source_payload: Mapping[str, Any] | str | None,
) -> TextSelection:
    """Select only text captured inside this immutable listing observation."""
    payload = _as_mapping(source_payload)
    announcement = _as_mapping(payload.get("announcement"))
    fields: list[tuple[str, str]] = []
    observed_title = announcement.get("raw_title")
    if isinstance(observed_title, str):
        title = observed_title
        title_path = "marketplace_listing_observations.source_payload_json.announcement.raw_title"
    else:
        title = ""
        title_path = "marketplace_listing_observations.source_payload_json.announcement.raw_title#missing"
    if title.strip():
        fields.append((title_path, title))

    explicit: dict[str, str] = {}
    # The current capture stores title, price/status and a slug here. Only fields
    # whose meaning is explicitly textual product evidence are selected.
    for key in ("description", "brand", "model", "category", "variant", "specification"):
        value = announcement.get(key)
        if isinstance(value, str) and value.strip() and value not in [text for _, text in fields]:
            path = f"marketplace_listing_observations.source_payload_json.announcement.{key}"
            fields.append((path, value))
            if key in {"brand", "model", "category", "variant"}:
                explicit[key] = value.strip()

    if not fields:
        return TextSelection(
            title_path, "", explicit,
        )
    field_path = fields[0][0] if len(fields) == 1 else json.dumps(
        [path for path, _ in fields], ensure_ascii=False, separators=(",", ":"),
    )
    return TextSelection(field_path, "\n".join(text for _, text in fields), explicit)


def extract_product_mentions(
    text: str,
    *,
    profile: NormalizationProfile,
    explicit_attributes: Mapping[str, str] | None = None,
) -> tuple[Classification, tuple[ProductMention, ...]]:
    """Classify then conservatively emit zero, one, or several supported mentions."""
    classification = classify_record(text, profile)
    if classification.value is not RecordClass.PRODUCT:
        return classification, ()

    components = _split_distinct_mentions(text)
    explicit = {str(k): str(v) for k, v in (explicit_attributes or {}).items()}
    mentions: list[ProductMention] = []
    for index, (start, end, raw_span) in enumerate(components):
        normalized = normalize_text(raw_span, profile)
        if not normalized.normalized_text:
            continue
        attributes = _extract_attributes(raw_span, explicit)
        mention_kind = "accessory" if _has_term(
            normalized.normalized_text, _ACCESSORY_TERMS,
        ) else "bundle" if _has_term(normalized.normalized_text, ("bundle", "lot", "pack")) else "product"
        mentions.append(ProductMention(
            mention_index=len(mentions),
            source_span_start=start,
            source_span_end=end,
            source_text_span=raw_span,
            normalized_text=normalized.normalized_text,
            language_code=normalized.language_code,
            mention_kind=mention_kind,
            brand=attributes["brand"],
            model=attributes["model"],
            category=attributes["category"],
            variant=attributes["variant"],
            quantity_value=attributes["quantity_value"],
            quantity_unit=attributes["quantity_unit"],
            capacity_value=attributes["capacity_value"],
            capacity_unit=attributes["capacity_unit"],
            specifications_json=attributes["specifications_json"],
            extraction_confidence="HIGH" if attributes["category"] else "UNCERTAIN",
        ))
    return classification, tuple(mentions)


def _extract_attributes(text: str, explicit: Mapping[str, str]) -> dict[str, Any]:
    category = _category_for_text(text)
    brand_match = _BRAND_RE.search(text)
    marked_model = _MODEL_MARKER_RE.search(text)
    identifier_model = _IDENTIFIER_RE.search(text) if marked_model is None else None
    normalized_text = normalize_text(text).normalized_text
    arabic_sprayer_context = _has_term(normalized_text, _ARABIC_SPRAYER_TERMS)
    crown_context = _CROWN_CONTEXT_RE.search(text) if arabic_sprayer_context else None
    crown_model = _CROWN_MODEL_RE.search(text) if crown_context else None
    variant_match = _VARIANT_RE.search(text)
    quantity_match = _QUANTITY_RE.search(text)
    capacity_matches = list(_CAPACITY_RE.finditer(text))
    other_specs = list(_OTHER_SPEC_RE.finditer(text))

    brand = (
        brand_match.group(1) if brand_match else
        "CROWN" if crown_context and crown_model else explicit.get("brand")
    )
    model = (
        marked_model.group(1) if marked_model else
        crown_model.group(0) if crown_model else
        identifier_model.group(0) if identifier_model else
        explicit.get("model")
    )
    variant = variant_match.group(1) if variant_match else explicit.get("variant")
    if explicit.get("category"):
        category = explicit["category"]
    capacity_value = None
    capacity_unit = None
    specifications: list[dict[str, str]] = []
    if capacity_matches:
        match = capacity_matches[0]
        capacity_value = _to_number(match.group(1))
        capacity_unit = match.group(2).casefold()
    for match in capacity_matches + other_specs:
        specifications.append({"value": match.group(1), "unit": match.group(2)})

    quantity_value = _to_number(quantity_match.group(1)) if quantity_match else None
    quantity_unit = quantity_match.group(2).casefold() if quantity_match else None
    return {
        "brand": brand,
        "model": model,
        "category": category,
        "variant": variant,
        "quantity_value": quantity_value,
        "quantity_unit": quantity_unit,
        "capacity_value": capacity_value,
        "capacity_unit": capacity_unit,
        "specifications_json": json.dumps(
            {"values": specifications}, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ) if specifications else None,
    }


def _to_number(value: str) -> float:
    return float(value.replace(",", "."))


def _split_distinct_mentions(text: str) -> list[tuple[int, int, str]]:
    pieces = list(_SPLIT_RE.finditer(text))
    if not pieces:
        trimmed = _trim_span(text, 0)
        return [trimmed] if trimmed[2] else []
    spans: list[tuple[int, int, str]] = []
    previous = 0
    for delimiter in pieces:
        spans.append(_trim_span(text[previous:delimiter.start()], previous))
        previous = delimiter.end()
    spans.append(_trim_span(text[previous:], previous))
    spans = [span for span in spans if span[2]]
    if len(spans) >= 2 and all(_is_distinct_reference(span[2]) for span in spans):
        return spans
    trimmed = _trim_span(text, 0)
    return [trimmed] if trimmed[2] else []


def _is_distinct_reference(text: str) -> bool:
    normalized = normalize_text(text).normalized_text
    return _category_for_text(normalized) is not None or _has_term(
        normalized, _ACCESSORY_TERMS + ("kit",),
    )


def _trim_span(text: str, offset: int) -> tuple[int, int, str]:
    start = len(text) - len(text.lstrip())
    end = len(text.rstrip())
    return offset + start, offset + end, text[start:end]


def _category_for_text(text: str) -> str | None:
    normalized = normalize_text(text).normalized_text
    for category, terms in _PRODUCT_CATEGORIES:
        if _has_term(normalized, terms):
            return category
    return None


def _has_term(text: str, terms: tuple[str, ...]) -> bool:
    return bool(_matched_terms(text, terms))


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for term in terms:
        escaped = re.escape(normalize_text(term).normalized_text)
        if re.search(rf"(?<!\w){escaped}(?!\w)", text, flags=re.UNICODE):
            found.append(term)
    return sorted(set(found))


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _control_text_key(value: str) -> str:
    visible = "".join(
        char for char in value
        if unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    )
    return "".join(char.casefold() for char in visible if char.isalnum())


def _line_records(text: str) -> list[tuple[str, int, int]]:
    records: list[tuple[str, int, int]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        visible = line.rstrip("\r\n")
        cleaned = " ".join(visible.split()).strip()
        if cleaned:
            left = len(visible) - len(visible.lstrip())
            right = len(visible.rstrip())
            records.append((cleaned, offset + left, offset + right))
        offset += len(line)
    return records


def _creative_line_after_sponsored(
    text: str, *, page_name: str | None,
) -> tuple[str, int, int] | None:
    lines = _line_records(text)
    sponsored_at = None
    for index, (line, _start, _end) in enumerate(lines):
        key = _control_text_key(line)
        if key == _SPONSORED_KEY or key.startswith("sponsoredpaidforby"):
            sponsored_at = index
            break
    if sponsored_at is None:
        return None
    excluded = {_control_text_key(label) for label in _GENERIC_CONTROL_LABELS}
    page_name_key = _control_text_key(page_name) if page_name else None
    for line, start, end in lines[sponsored_at + 1:]:
        key = _control_text_key(line)
        if not key or key in excluded or key == page_name_key:
            continue
        if key.startswith("paidforby"):
            continue
        if key.startswith((
            "thisadwasrunbyanaccountorpagewelaterdisabled",
            "thisadisnotactiveforsomeaudiences",
        )):
            continue
        if re.fullmatch(r"\d+ads?usethiscreativeandtext", key):
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}(?:\s*/\s*\d{1,2}:\d{2})?", line):
            continue
        if re.fullmatch(r"(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+", line, re.IGNORECASE):
            return None
        if line.startswith(("http://", "https://")):
            return None
        return text[start:end], start, end
    return None


def _safe_standalone_creative(value: str, *, page_name: str | None) -> str:
    for line, _start, _end in _line_records(value):
        key = _control_text_key(line)
        if not key or key in {_control_text_key(label) for label in _GENERIC_CONTROL_LABELS}:
            continue
        if page_name and key == _control_text_key(page_name):
            continue
        if key.startswith("paidforby") or line.startswith(("http://", "https://")):
            continue
        return line
    return ""
