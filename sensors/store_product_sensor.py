"""Pure, bounded extraction of product facts from supplied storefront HTML.

This module performs no network or database I/O. Callers must validate and fetch
the page through an approved fetcher, then pass the retrieved HTML and its URL.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit


@dataclass(frozen=True)
class FieldEvidence:
    value: Any
    method: str
    source: str


@dataclass(frozen=True)
class ProductRecord:
    source_url: str
    retrieved_at: str
    extraction_method: str
    fields: Mapping[str, Any]
    provenance: Mapping[str, tuple[FieldEvidence, ...]]
    unresolved: Mapping[str, tuple[Any, ...]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "extraction_method": self.extraction_method,
            "fields": dict(self.fields),
            "provenance": {
                key: [asdict(item) for item in items]
                for key, items in self.provenance.items()
            },
            "unresolved": {key: list(values) for key, values in self.unresolved.items()},
        }


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.scripts: list[str] = []
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self._capture: str | None = None
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "meta":
            self.metas.append(values)
        elif tag == "link":
            self.links.append(values)
        elif tag == "script" and values.get("type", "").split(";")[0].strip().lower() == "application/ld+json":
            self._capture, self._script_parts = "script", []
        elif tag == "title":
            self._capture = "title"
        elif tag == "h1":
            self._capture = "h1"

    def handle_data(self, data: str) -> None:
        if self._capture == "script":
            self._script_parts.append(data)
        elif self._capture == "title":
            self.title_parts.append(data)
        elif self._capture == "h1":
            self.h1_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script" and self._capture == "script":
            self.scripts.append("".join(self._script_parts))
            self._capture = None
        elif (tag == "title" and self._capture == "title") or (tag == "h1" and self._capture == "h1"):
            self._capture = None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _walk(value: Any):
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            if isinstance(item, (Mapping, list)):
                yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _is_product(node: Mapping[str, Any]) -> bool:
    raw = node.get("@type")
    types = raw if isinstance(raw, list) else [raw]
    return any(isinstance(t, str) and t.rstrip("/").rsplit("/", 1)[-1].lower() == "product" for t in types)


def _absolute_urls(value: Any, base: str) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if isinstance(item, Mapping):
            item = item.get("url") or item.get("contentUrl")
        url = _text(item)
        if url:
            result.append(urljoin(base, url))
    return list(dict.fromkeys(result))


def _canonical_page_url(source_url: str, parser: _PageParser) -> str | None:
    for link in parser.links:
        if "canonical" in link.get("rel", "").lower().split():
            href = link.get("href", "").strip()
            if href:
                return urljoin(source_url, href)
    return None


def extract_store_product(html: str, source_url: str, *, retrieved_at: str | datetime | None = None) -> tuple[ProductRecord, ...]:
    """Extract independent product candidates from already-fetched HTML.

    Unsafe or malformed source URLs are rejected. This function never fetches a
    URL, so redirect and network destination policy remain the caller's duty.
    """
    if not isinstance(html, str) or not html.strip():
        return ()
    if not isinstance(source_url, str):
        raise ValueError("source_url must be an absolute HTTP(S) URL")
    parts = urlsplit(source_url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("source_url must be an absolute HTTP(S) URL without credentials")
    stamp = datetime.now(timezone.utc) if retrieved_at is None else (
        retrieved_at if isinstance(retrieved_at, datetime) else datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    )
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("retrieved_at must include timezone information")
    stamp_text = stamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    parser = _PageParser()
    parser.feed(html)
    parser.close()
    canonical = _canonical_page_url(source_url, parser)
    meta: dict[str, list[str]] = {}
    for item in parser.metas:
        key = (item.get("property") or item.get("name") or item.get("itemprop") or "").strip().lower()
        value = _text(item.get("content"))
        if key and value:
            meta.setdefault(key, []).append(value)
    json_nodes: list[Mapping[str, Any]] = []
    for script in parser.scripts:
        try:
            json_nodes.extend(node for node in _walk(json.loads(script)) if isinstance(node, Mapping) and _is_product(node))
        except (json.JSONDecodeError, TypeError):
            continue
    # Deduplicate the same Product repeated across JSON-LD blocks, but retain variants.
    products: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for product in json_nodes:
        identity = json.dumps(product, sort_keys=True, ensure_ascii=False, default=str)
        if identity not in seen:
            seen.add(identity)
            products.append(product)
    if not products:
        is_product_page = any(v.lower() == "product" for v in meta.get("og:type", []) + meta.get("twitter:card", [])) or bool(meta.get("product:price:amount") or meta.get("product:price:currency"))
        if not is_product_page:
            return ()
        products = [{}]

    def meta_first(*keys: str) -> str | None:
        for key in keys:
            if meta.get(key):
                return meta[key][0]
        return None

    output: list[ProductRecord] = []
    for product in products:
        fields: dict[str, Any] = {"product_url": source_url, "canonical_url": canonical}
        evidence: dict[str, tuple[FieldEvidence, ...]] = {
            "product_url": (FieldEvidence(source_url, "request_context", "source_url"),)
        }
        if canonical:
            evidence["canonical_url"] = (FieldEvidence(canonical, "link[rel=canonical]", "HTML"),)
        unresolved: dict[str, tuple[Any, ...]] = {}

        def choose(field: str, candidates: list[tuple[Any, str, str]]) -> None:
            candidates = [(v, m, s) for v, m, s in candidates if v not in (None, "", [], {})]
            # Identical values from independent markup sources are corroborating evidence.
            distinct: list[Any] = []
            for value, _, _ in candidates:
                if value not in distinct:
                    distinct.append(value)
            if len(distinct) > 1:
                unresolved[field] = tuple(distinct)
                evidence[field] = tuple(FieldEvidence(v, m, s) for v, m, s in candidates)
                fields[field] = None
            elif distinct:
                fields[field] = distinct[0]
                evidence[field] = tuple(FieldEvidence(v, m, s) for v, m, s in candidates)

        title_candidates = []
        name = _text(product.get("name"))
        if name: title_candidates.append((name, "json_ld", "Product.name"))
        for key in ("og:title", "twitter:title"):
            title_candidates.extend((v, "meta", key) for v in meta.get(key, []))
        if not title_candidates and not json_nodes:
            title = _text(" ".join(parser.h1_parts))
            if title: title_candidates.append((title, "product_meta_h1", "h1"))
        choose("name", title_candidates)

        for field, nodes, metas in (
            ("brand", [(_text(product.get("brand", {}).get("name") if isinstance(product.get("brand"), Mapping) else product.get("brand")), "json_ld", "Product.brand")], ("product:brand", "brand")),
            ("model", [(_text(product.get("model")), "json_ld", "Product.model")], ("product:model", "model")),
            ("sku", [(_text(product.get("sku")), "json_ld", "Product.sku")], ("product:sku", "sku")),
            ("mpn", [(_text(product.get("mpn")), "json_ld", "Product.mpn")], ("product:mpn", "mpn")),
            ("gtin", [(_text(next((product.get(k) for k in ("gtin", "gtin8", "gtin12", "gtin13", "gtin14") if product.get(k) is not None), None)), "json_ld", "Product.gtin")], ("product:gtin", "gtin")),
            ("description", [(_text(product.get("description")), "json_ld", "Product.description")], ("og:description", "description")),
        ):
            candidates = [(value, method, src) for value, method, src in nodes if value]
            for key in metas:
                candidates.extend((v, "meta", key) for v in meta.get(key, []))
            choose(field, candidates)

        images = _absolute_urls(product.get("image"), source_url)
        if images:
            fields["image_urls"] = images
            evidence["image_urls"] = tuple(FieldEvidence(v, "json_ld", "Product.image") for v in images)
        elif meta.get("og:image"):
            images = _absolute_urls(meta["og:image"], source_url)
            fields["image_urls"] = images
            evidence["image_urls"] = tuple(FieldEvidence(v, "meta", "og:image") for v in images)

        option_fields = {
            key: product[key]
            for key in ("color", "size", "material", "pattern", "additionalProperty", "variesBy")
            if product.get(key) not in (None, "", [], {})
        }
        if option_fields:
            fields["options"] = option_fields
            evidence["options"] = tuple(
                FieldEvidence(value, "json_ld", f"Product.{key}")
                for key, value in option_fields.items()
            )

        offers = product.get("offers")
        offers = offers if isinstance(offers, list) else [offers]
        valid_offers = [o for o in offers if isinstance(o, Mapping)]
        if valid_offers:
            # Each Offer remains separate; no cross-offer price/currency merging.
            fields["offers"] = [
                {"price": _text(o.get("price") if o.get("price") is not None else o.get("lowPrice")),
                 "currency": _text(o.get("priceCurrency")),
                 "availability": _text(o.get("availability")),
                 "url": _text(o.get("url")) and urljoin(source_url, _text(o.get("url"))),
                 "sku": _text(o.get("sku"))}
                for o in valid_offers
            ]
            evidence["offers"] = tuple(FieldEvidence(dict(o), "json_ld", "Product.offers") for o in valid_offers)
        else:
            amount = meta_first("product:price:amount", "og:price:amount")
            currency = meta_first("product:price:currency", "og:price:currency")
            availability = meta_first("product:availability")
            if amount or currency or availability:
                fields["offers"] = [{"price": amount, "currency": currency, "availability": availability, "url": None, "sku": None}]
                evidence["offers"] = (FieldEvidence(fields["offers"][0], "product_meta", "product:*"),)

        # Preserve explicitly exposed variant entities as independent candidates.
        variants = product.get("hasVariant")
        if isinstance(variants, Mapping): variants = [variants]
        if isinstance(variants, list) and variants:
            fields["variants"] = variants
            evidence["variants"] = tuple(FieldEvidence(dict(v), "json_ld", "Product.hasVariant") for v in variants if isinstance(v, Mapping))
        output.append(ProductRecord(source_url, stamp_text, "json_ld" if product else "product_metadata", fields, evidence, unresolved))
    return tuple(output)


def safe_absolute_page_url(value: str) -> bool:
    """Syntactic URL guard for caller-side policy; does not resolve public IPs."""
    try:
        parts = urlsplit(value)
        return parts.scheme.lower() in {"http", "https"} and bool(parts.hostname) and not parts.username and not parts.password
    except (TypeError, ValueError):
        return False
