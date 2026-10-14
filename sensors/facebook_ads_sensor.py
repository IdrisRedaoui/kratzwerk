"""Observe Facebook Ad Library ads in Foundation V2.

The default command performs a read-only browser validation. Database writes
require the explicit ``--capture`` flag.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import argparse
import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from config import connect


logger = logging.getLogger(__name__)

PLATFORM = "facebook"
COUNTRY = "DZ"
KEYWORD = "Peinture"
BASE_URL = "https://www.facebook.com/ads/library/"
MAX_SCROLL_STEPS = 12
IDLE_SCROLL_STEPS_TO_STOP = 2
REQUEST_TIMEOUT_MS = 60_000
DOM_STABILITY_QUIET_MS = 800
DOM_SNAPSHOT_ATTEMPTS = 3

LIBRARY_ID_RE = re.compile(
    r"(?:Library\s*ID|رقم\s*تعريف\s*المكتبة)\s*[:#\s]*([0-9]+)",
    re.IGNORECASE,
)
START_DATE_RE = re.compile(
    r"(?:Started\s+running\s+on|Started\s+running|بدأ\s+التشغيل\s+في)\s*[:：]?\s*(.+)",
    re.IGNORECASE,
)
TRACKING_QUERY_KEYS = {"__tn__", "ref", "refid", "tracking", "source"}
SEARCH_QUERY_KEYS = {
    "active_status", "ad_type", "country", "media_type", "q", "search_type"
}
STATUS_PATTERNS = (
    (re.compile(r"^(?:inactive|not\s+active|not\s+delivering|غير\s+نشط|غير\s+فعّال)$", re.I), "inactive"),
    (re.compile(r"^(?:active|currently\s+active|نشط|فعّال)$", re.I), "active"),
)
@dataclass
class CaptureStats:
    discovered: int = 0
    unique_ads: int = 0
    duplicate_inputs: int = 0
    new_ads: int = 0
    existing_ads: int = 0
    observations: int = 0
    skipped_without_identity: int = 0


@dataclass
class CoverageStats:
    search_url: str
    page_title: Optional[str] = None
    final_url: Optional[str] = None
    scroll_steps: int = 0
    unique_ads_by_scroll: list[int] = field(default_factory=list)
    final_dom_cards: int = 0
    duplicate_cards_in_final_dom: int = 0
    pagination_controls: list[str] = field(default_factory=list)
    stopped_after_idle_steps: bool = False
    page_notice: Optional[str] = None


@dataclass
class LiveResult:
    ads: list[dict[str, Any]]
    coverage: CoverageStats


def utc_timestamp(value: Optional[str | datetime] = None) -> str:
    """Return an ISO-8601 UTC timestamp; reject naive supplied values."""
    if value is None:
        stamp = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("observed_at must have timezone semantics")
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def build_search_url(keyword: str = KEYWORD, country: str = COUNTRY) -> str:
    params = {
        "active_status": "all",
        "ad_type": "all",
        "country": country,
        "q": keyword,
        "search_type": "keyword_unordered",
        "media_type": "all",
    }
    return f"{BASE_URL}?{urlencode(params)}"


def canonical_ad_url(raw_url: Optional[str]) -> Optional[str]:
    """Normalize an ad-specific Ad Library link, dropping tracking parameters."""
    if not raw_url or not isinstance(raw_url, str):
        return None
    absolute = urljoin(BASE_URL, raw_url.strip())
    parts = urlsplit(absolute)
    host = (parts.hostname or "").lower()
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return None
    if not parts.path.rstrip("/").endswith("/ads/library"):
        return None
    params = parse_qs(parts.query, keep_blank_values=True)
    if not params or set(params).issubset(SEARCH_QUERY_KEYS | TRACKING_QUERY_KEYS):
        return None
    clean = [
        (key, item)
        for key in sorted(params)
        if key not in TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
        for item in params[key]
    ]
    if not clean:
        return None
    return urlunsplit(("https", "www.facebook.com", "/ads/library/", urlencode(clean), ""))


def library_id_from_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    try:
        values = parse_qs(urlsplit(urljoin(BASE_URL, raw_url)).query)
    except ValueError:
        return None
    for key in ("id", "ad_id", "library_id"):
        found = values.get(key)
        if found and found[0].strip():
            return found[0].strip()
    return None


def normalize_status(source_status: Any, card_lines: Sequence[str] = ()) -> str:
    """Map an explicit Facebook status label; a missing label remains unknown."""
    candidates = [source_status] if source_status is not None else []
    candidates.extend(card_lines)
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        label = " ".join(candidate.split()).strip(" .:：")
        for pattern, status in STATUS_PATTERNS:
            if pattern.fullmatch(label):
                return status
    return "unknown"


def active_value(status: str) -> Optional[int]:
    return {"active": 1, "inactive": 0}.get(status)


def parse_start_date(raw_date: Optional[str]) -> Optional[str]:
    """Parse common displayed start dates without substituting a guessed date."""
    if not raw_date:
        return None
    value = " ".join(str(raw_date).split()).strip(" .,;:：")
    normalized = value.replace("Sept ", "Sep ")
    formats = (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
    )
    for date_format in formats:
        try:
            return datetime.strptime(normalized, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _clean_lines(text: str) -> list[str]:
    return [" ".join(line.split()).strip() for line in text.splitlines() if line.strip()]


def _ad_url_and_id(raw_ad: Mapping[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return stable Library ID, canonical URL, and original URL evidence."""
    library_id = raw_ad.get("library_id") or raw_ad.get("id")
    library_id = str(library_id).strip() if library_id is not None else None
    if not library_id:
        text = raw_ad.get("text") or raw_ad.get("card_text") or ""
        match = LIBRARY_ID_RE.search(text) if isinstance(text, str) else None
        library_id = match.group(1) if match else None

    candidates: list[str] = []
    source_url = raw_ad.get("source_url") or raw_ad.get("url")
    if isinstance(source_url, str) and source_url.strip():
        candidates.append(source_url.strip())
    for link in raw_ad.get("links", ()) or ():
        if isinstance(link, str):
            candidates.append(link)
        elif isinstance(link, Mapping) and isinstance(link.get("href"), str):
            candidates.append(link["href"])

    selected_raw = None
    canonical = None
    for candidate in candidates:
        candidate_url = canonical_ad_url(candidate)
        if candidate_url:
            selected_raw = candidate
            canonical = candidate_url
            url_id = library_id_from_url(candidate)
            if not library_id and url_id:
                library_id = url_id
            break

    if library_id and canonical is None:
        canonical = f"{BASE_URL}?{urlencode({'id': library_id})}"
    return library_id, canonical, selected_raw


def _creative_fingerprint(ad: Mapping[str, Any]) -> Optional[str]:
    material = {
        "creative_text": ad.get("creative_text"),
        "image_urls": ad.get("image_urls") or [],
        "video_urls": ad.get("video_urls") or [],
        "landing_url": ad.get("landing_url"),
    }
    if not material["creative_text"] and not material["image_urls"] and not material["video_urls"] and not material["landing_url"]:
        return None
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_ad(raw_ad: Mapping[str, Any], *, keyword: str = KEYWORD, country: str = COUNTRY) -> Optional[dict[str, Any]]:
    """Convert one source card to the V2 field set; unknown fields stay null."""
    library_id, url, selected_raw_url = _ad_url_and_id(raw_ad)
    if not library_id and not url:
        return None

    text = raw_ad.get("text") or raw_ad.get("card_text") or ""
    lines = _clean_lines(text) if isinstance(text, str) else []
    raw_start = raw_ad.get("start_date_text")
    if raw_start is None:
        for line in lines:
            match = START_DATE_RE.search(line)
            if match:
                raw_start = match.group(1).strip()
                break
    source_status = raw_ad.get("source_status")
    if source_status is None:
        source_status = raw_ad.get("status")
    status = normalize_status(source_status, lines)

    source_platforms = raw_ad.get("platforms")
    if source_platforms is None:
        source_platforms = []
    elif isinstance(source_platforms, str):
        source_platforms = [source_platforms]
    platforms = sorted({str(value).strip().lower() for value in source_platforms if str(value).strip()})

    landing_url = raw_ad.get("landing_url")
    page_name = raw_ad.get("page_name")
    page_source_id = raw_ad.get("page_source_id")
    creative_text = raw_ad.get("creative_text")
    image_urls = raw_ad.get("image_urls") or []
    video_urls = raw_ad.get("video_urls") or []
    metadata = {
        "library_id": library_id,
        "url": url,
        "source_url": selected_raw_url or raw_ad.get("source_url") or raw_ad.get("url"),
        "page_source_id": page_source_id,
        "page_name": page_name,
        "platforms": platforms,
        "creative_text": creative_text,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "landing_url": landing_url,
        "start_date_text": raw_start,
        "start_date": parse_start_date(raw_start),
        "source_status": source_status,
        "status": status,
        "capture_query": {"keyword": keyword, "country": country},
    }
    result = {
        "library_id": library_id,
        "url": url,
        "page_source_id": page_source_id,
        "page_name": page_name,
        "platforms": platforms,
        "creative_text": creative_text,
        "creative_fingerprint": _creative_fingerprint(
            {"creative_text": creative_text, "image_urls": image_urls,
             "video_urls": video_urls, "landing_url": landing_url}
        ),
        "start_date": metadata["start_date"],
        "source_status": source_status,
        "status": status,
        "metadata": metadata,
        "raw_payload": raw_ad.get("raw_payload", raw_ad.get("source_payload", raw_ad)),
    }
    return result


def _ad_key(ad: Mapping[str, Any]) -> tuple[str, str]:
    if ad.get("library_id"):
        return ("library_id", str(ad["library_id"]))
    return ("url", str(ad["url"]))


def store_capture(
    ads: Iterable[Mapping[str, Any]],
    observed_at: Optional[str | datetime] = None,
    *,
    keyword: str = KEYWORD,
    country: str = COUNTRY,
    db_connect=connect,
) -> CaptureStats:
    """Atomically upsert canonical ads and append one observation per unique ad."""
    captured_at = utc_timestamp(observed_at)
    raw_ads = list(ads)
    stats = CaptureStats(discovered=len(raw_ads))
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for raw_ad in raw_ads:
        ad = normalize_ad(raw_ad, keyword=keyword, country=country)
        if ad is None:
            stats.skipped_without_identity += 1
            logger.error("Skipping Facebook ad card without a stable ID or ad-specific URL.")
            continue
        key = _ad_key(ad)
        if key in seen:
            stats.duplicate_inputs += 1
            continue
        seen.add(key)
        normalized.append(ad)
    stats.unique_ads = len(normalized)

    conn = db_connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for ad in normalized:
            library_id = ad["library_id"]
            url = ad["url"]
            row = None
            if library_id:
                row = conn.execute(
                    "SELECT id, library_id, url FROM facebook_ads WHERE platform = ? AND library_id = ?",
                    (PLATFORM, library_id),
                ).fetchone()
            if row is None and url:
                row = conn.execute(
                    "SELECT id, library_id, url FROM facebook_ads WHERE url = ?", (url,)
                ).fetchone()

            status = ad["status"]
            source_payload = json.dumps(
                {"metadata": ad["metadata"], "raw_source_payload": ad["raw_payload"]},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            platforms_json = json.dumps(ad["platforms"], ensure_ascii=False, separators=(",", ":"))

            if row is None:
                cursor = conn.execute(
                    """INSERT INTO facebook_ads
                       (library_id, url, page_source_id, page_name, platform, platforms_json,
                        creative_text, creative_fingerprint, first_seen_at, last_seen_at,
                        start_date, status, source_payload_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        library_id, url, ad["page_source_id"], ad["page_name"], PLATFORM,
                        platforms_json, ad["creative_text"], ad["creative_fingerprint"],
                        captured_at, captured_at, ad["start_date"], status, source_payload,
                    ),
                )
                ad_id = cursor.lastrowid
                stats.new_ads += 1
            else:
                ad_id = row["id"]
                old_library_id = row["library_id"]
                if library_id and old_library_id and old_library_id != library_id:
                    raise sqlite3.IntegrityError(
                        f"Facebook ad URL identity conflict: {url} belongs to Library ID {old_library_id}, not {library_id}"
                    )
                conn.execute(
                    """UPDATE facebook_ads
                       SET library_id = COALESCE(library_id, ?),
                           url = COALESCE(?, url),
                           page_source_id = COALESCE(?, page_source_id),
                           page_name = COALESCE(?, page_name),
                           platforms_json = ?,
                           creative_text = COALESCE(?, creative_text),
                           creative_fingerprint = COALESCE(?, creative_fingerprint),
                           last_seen_at = ?,
                           start_date = COALESCE(?, start_date),
                           status = ?,
                           source_payload_json = ?
                       WHERE id = ?""",
                    (
                        library_id, url, ad["page_source_id"], ad["page_name"],
                        platforms_json, ad["creative_text"], ad["creative_fingerprint"],
                        captured_at, ad["start_date"], status, source_payload, ad_id,
                    ),
                )
                stats.existing_ads += 1

            observation_payload = json.dumps(
                {
                    **ad["metadata"],
                    "raw_source_payload": ad["raw_payload"],
                    "identity": {
                        "method": "facebook_library_id" if library_id else "canonical_ad_url",
                        "library_id": library_id,
                        "url": url,
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.execute(
                """INSERT INTO facebook_ad_observations
                   (ad_id, observed_at, active, detected_metadata_json, creative_fingerprint)
                   VALUES (?, ?, ?, ?, ?)""",
                (ad_id, captured_at, active_value(status), observation_payload, ad["creative_fingerprint"]),
            )
            stats.observations += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return stats


DOM_CARD_SCRIPT = r"""() => {
  const idPattern = /(?:Library\s*ID|رقم\s*تعريف\s*المكتبة)\s*[:#\s]*(\d+)/i;
  const idNodes = Array.from(document.querySelectorAll('div')).filter(el => el.innerText && idPattern.test(el.innerText));
  const idLeaves = idNodes.filter(el => !Array.from(el.querySelectorAll('div')).some(
    child => child.innerText && idPattern.test(child.innerText)
  ));
  const detailButtons = Array.from(document.querySelectorAll('button,[role="button"]')).filter(el =>
    /^see ad details$/i.test((el.innerText || el.getAttribute('aria-label') || '').trim())
  );
  const candidates = [...detailButtons, ...idLeaves];
  const roots = [];
  const seen = new Set();
  for (const marker of candidates) {
    let chosen = null;
    let fallback = marker.parentElement;
    let node = marker;
    for (let depth = 0; depth < 10 && node; depth++, node = node.parentElement) {
      const text = node.innerText || '';
      const idCount = (text.match(/(?:Library\s*ID|رقم\s*تعريف\s*المكتبة)\s*[:#\s]*\d+/gi) || []).length;
      const detailCount = Array.from(node.querySelectorAll('button,[role="button"]')).filter(b =>
        /^see ad details$/i.test((b.innerText || b.getAttribute('aria-label') || '').trim())
      ).length;
      const hasSourceContent = node.querySelectorAll('a,img,video').length > 0;
      const isOneCard = idCount === 1 || (idCount === 0 && detailCount === 1);
      if (isOneCard && hasSourceContent && text.length >= 100) {
        chosen = node;
        break;
      }
      if (fallback && text.length > (fallback.innerText || '').length && text.length < 2000) fallback = node;
    }
    const root = chosen || fallback;
    if (root && !seen.has(root)) {
      seen.add(root);
      roots.push(root);
    }
  }
  const cards = roots.map(el => {
    const links = Array.from(el.querySelectorAll('a')).map(a => ({
      href: a.href || a.getAttribute('href'),
      text: (a.innerText || '').trim(),
      aria_label: a.getAttribute('aria-label')
    }));
    const buttons = Array.from(el.querySelectorAll('button,[role="button"]')).map(b => ({
      text: (b.innerText || '').trim(),
      aria_label: b.getAttribute('aria-label')
    }));
    const images = Array.from(el.querySelectorAll('img')).map(i => ({
      src: i.currentSrc || i.src || i.getAttribute('src'),
      alt: i.alt || null
    }));
    const videos = Array.from(el.querySelectorAll('video')).map(v => ({
      src: v.currentSrc || v.src || null,
      poster: v.poster || null
    }));
    const attrs = {};
    for (const node of [el, ...Array.from(el.querySelectorAll('[data-ad-preview], [data-page-name], [data-page-id]'))]) {
      for (const attr of Array.from(node.attributes || [])) {
        if (/^(data-(ad|page)|aria-label)/i.test(attr.name)) attrs[attr.name] = attr.value;
      }
    }
    const platformLabels = Array.from(el.querySelectorAll('[aria-label],[title]'))
      .flatMap(node => [node.getAttribute('aria-label'), node.getAttribute('title')])
      .filter(value => value && /^(?:Facebook|Instagram|Messenger|Audience Network|Threads)$/i.test(value.trim()));
    return {text: el.innerText, links, buttons, images, videos, attributes: attrs, platform_labels: platformLabels};
  });
  const controls = Array.from(document.querySelectorAll('button,[role="button"]'))
    .map(el => (el.innerText || el.getAttribute('aria-label') || '').trim())
    .filter(text => /^(?:next page|previous page|load more results|show more results|عرض نتائج إضافية)$/i.test(text));
  const body = (document.body && document.body.innerText || '').slice(0, 2000);
  return {cards, controls, body};
}"""


INITIAL_RESULTS_SCRIPT = r"""() => {
  const text = (document.body && document.body.innerText) || '';
  return /(?:Library\s*ID|رقم\s*تعريف\s*المكتبة)\s*[:#\s]*\d+/i.test(text) ||
    /(?:no\s+(?:ads|results)|didn.t\s+match\s+any\s+ads|لا\s*توجد\s*نتائج)/i.test(text);
}"""


DOM_STABILITY_SCRIPT = r"""quietMs => {
  if (document.readyState !== 'complete' || !document.documentElement || !document.body) {
    return false;
  }
  const key = '__kratzwerkFacebookSensorDomState';
  const now = performance.now();
  let state = window[key];
  if (!state || state.root !== document.documentElement) {
    state = {root: document.documentElement, lastMutationAt: now};
    const observer = new MutationObserver(() => { state.lastMutationAt = performance.now(); });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true
    });
    window[key] = state;
  }
  return now - state.lastMutationAt >= quietMs;
}"""


def _is_navigation_context_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "execution context was destroyed" in message or (
        "context was destroyed" in message and "navigation" in message
    )


def _wait_for_stable_page(page: Any, *, wait_for_results: bool = False) -> None:
    """Wait for the current document and rendered DOM to settle before reading it."""
    page.wait_for_load_state("domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    page.locator("body").wait_for(state="attached", timeout=REQUEST_TIMEOUT_MS)
    if wait_for_results:
        page.wait_for_function(INITIAL_RESULTS_SCRIPT, timeout=REQUEST_TIMEOUT_MS)
    page.wait_for_function(
        DOM_STABILITY_SCRIPT,
        arg=DOM_STABILITY_QUIET_MS,
        timeout=REQUEST_TIMEOUT_MS,
        polling="raf",
    )


def _read_stable_dom(page: Any, *, wait_for_results: bool = False) -> dict[str, Any]:
    """Take one DOM snapshot, recovering only from a navigation-destroyed context."""
    for attempt in range(DOM_SNAPSHOT_ATTEMPTS):
        try:
            _wait_for_stable_page(page, wait_for_results=wait_for_results)
            return page.evaluate(DOM_CARD_SCRIPT)
        except Exception as exc:
            if not _is_navigation_context_error(exc) or attempt + 1 == DOM_SNAPSHOT_ATTEMPTS:
                raise
            logger.info(
                "Facebook page navigation interrupted DOM extraction; "
                "waiting for the new document before retrying snapshot (%s/%s).",
                attempt + 1,
                DOM_SNAPSHOT_ATTEMPTS - 1,
            )
    raise AssertionError("unreachable: DOM snapshot attempts are bounded")


def _extract_card(card: Mapping[str, Any]) -> dict[str, Any]:
    text = card.get("text") if isinstance(card.get("text"), str) else ""
    lines = _clean_lines(text)
    id_match = LIBRARY_ID_RE.search(text)
    links = card.get("links") or []
    source_url = None
    for link in links:
        if not isinstance(link, Mapping):
            continue
        candidate = link.get("href")
        if canonical_ad_url(candidate):
            source_url = candidate
            break

    page_name = None
    page_source_id = None
    for link in links:
        if not isinstance(link, Mapping):
            continue
        href = link.get("href")
        label = (link.get("text") or link.get("aria_label") or "").strip()
        if isinstance(href, str):
            page_parts = urlsplit(urljoin(BASE_URL, href))
            page_host = (page_parts.hostname or "").lower()
            if page_host.endswith("facebook.com") and not canonical_ad_url(href):
                values = parse_qs(page_parts.query)
                if page_source_id is None:
                    page_source_id = values.get("page_id", [None])[0]
                    if page_source_id is None and page_parts.path == "/profile.php":
                        page_source_id = values.get("id", [None])[0]
        if not href or not label or canonical_ad_url(href):
            continue
        host = (urlsplit(urljoin(BASE_URL, href)).hostname or "").lower()
        if page_name is None and host.endswith("facebook.com") and not re.search(r"^(?:see more|sponsored|facebook)$", label, re.I):
            page_name = label
    if page_name is None:
        page_name = next((item.get("alt") for item in card.get("images", [])
                          if isinstance(item, Mapping) and item.get("alt")), None)

    image_urls = sorted({
        item.get("src") for item in card.get("images", [])
        if isinstance(item, Mapping) and isinstance(item.get("src"), str) and item.get("src")
    })
    video_urls = sorted({
        value for item in card.get("videos", []) if isinstance(item, Mapping)
        for value in (item.get("src"), item.get("poster")) if isinstance(value, str) and value
    })
    landing_url = None
    for link in links:
        if not isinstance(link, Mapping) or not isinstance(link.get("href"), str):
            continue
        candidate = urljoin(BASE_URL, link["href"])
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
        if host.endswith("facebook.com") and parts.path == "/l.php":
            destination = parse_qs(parts.query).get("u")
            if destination and destination[0]:
                landing_url = destination[0]
                break
        if host and host != "facebook.com" and not host.endswith(".facebook.com"):
            landing_url = candidate
            break

    raw_date = None
    for line in lines:
        match = START_DATE_RE.search(line)
        if match:
            raw_date = match.group(1).strip()
            break
    status = next((line for line in lines if normalize_status(line) != "unknown"), None)
    creative_text = _creative_text_from_card(text, page_name=page_name)
    return {
        "library_id": id_match.group(1) if id_match else library_id_from_url(source_url),
        "source_url": source_url,
        "page_name": page_name,
        "page_source_id": page_source_id,
        "platforms": card.get("platform_labels") or [],
        "creative_text": creative_text,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "landing_url": landing_url,
        "start_date_text": raw_date,
        "source_status": status,
        "text": text,
        "links": links,
        "raw_payload": {
            "card_text": text,
            "links": links,
            "buttons": card.get("buttons") or [],
            "images": card.get("images") or [],
            "videos": card.get("videos") or [],
            "attributes": card.get("attributes") or {},
        },
    }


def _control_text_key(value: str) -> str:
    """Normalize invisible controls and punctuation for UI-label comparisons."""
    visible = "".join(
        char for char in value
        if unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    )
    return "".join(char.casefold() for char in visible if char.isalnum())


def _creative_text_from_card(text: str, *, page_name: Optional[str]) -> Optional[str]:
    """Extract the first ad-copy line after Facebook's Sponsored card marker."""
    lines = _clean_lines(text)
    sponsored_at = None
    for index, line in enumerate(lines):
        key = _control_text_key(line)
        if key == "sponsored" or key.startswith("sponsoredpaidforby"):
            sponsored_at = index
            break
    if sponsored_at is None:
        return None

    control_labels = {
        "open dropdown", "see ad details", "see summary details", "sponsored",
        "platforms", "categories", "eu transparency", "play video", "play",
        "settings", "enter fullscreen", "unmute", "learn more", "shop now",
        "sign up", "like page", "follow", "send message", "call now", "see more",
        "next items", "previous items", "watch more", "continue watching",
        "continuer à regarder", "read more", "download", "install now", "apply now",
        "book now", "contact us", "get quote",
    }
    excluded_keys = {_control_text_key(label) for label in control_labels}
    page_name_key = _control_text_key(page_name) if page_name else None

    for line in lines[sponsored_at + 1:]:
        candidate = " ".join(line.split()).strip()
        key = _control_text_key(candidate)
        if not key or key in excluded_keys or key == page_name_key:
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
        if re.fullmatch(r"\d{1,2}:\d{2}(?:\s*/\s*\d{1,2}:\d{2})?", candidate):
            continue
        if re.fullmatch(r"(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+", candidate, re.IGNORECASE):
            # The site/headline block follows the primary copy; don't promote it.
            return None
        if candidate.startswith(("http://", "https://")):
            return None
        return candidate
    return None


def _browser_context(browser: Any) -> Any:
    return browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )


def collect_live_ads(
    *, keyword: str = KEYWORD, country: str = COUNTRY, headless: bool = True
) -> LiveResult:
    """Read Ad Library cards through the existing Playwright/browser workflow."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Live browser validation requires the Playwright Python package."
        ) from exc
    search_url = build_search_url(keyword, country)
    coverage = CoverageStats(search_url=search_url)
    cumulative: dict[tuple[str, str], dict[str, Any]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = _browser_context(browser)
            page = context.new_page()
            page.goto(search_url, timeout=REQUEST_TIMEOUT_MS, wait_until="domcontentloaded")
            idle_steps = 0
            last_count = 0
            for step in range(MAX_SCROLL_STEPS + 1):
                snapshot = _read_stable_dom(page, wait_for_results=(step == 0))
                cards = snapshot.get("cards", [])
                coverage.final_dom_cards = len(cards)
                normalized_snapshot = [_extract_card(card) for card in cards]
                snapshot_keys: set[tuple[str, str]] = set()
                duplicates_here = 0
                for ad in normalized_snapshot:
                    normalized = normalize_ad(ad, keyword=keyword, country=country)
                    if not normalized:
                        continue
                    key = _ad_key(normalized)
                    if key in snapshot_keys:
                        duplicates_here += 1
                        continue
                    snapshot_keys.add(key)
                    cumulative.setdefault(key, ad)
                coverage.duplicate_cards_in_final_dom = duplicates_here
                coverage.unique_ads_by_scroll.append(len(cumulative))
                coverage.pagination_controls = list(dict.fromkeys(snapshot.get("controls", [])))
                body = snapshot.get("body") or ""
                if body and not cumulative:
                    coverage.page_notice = body[:1000]
                if step >= MAX_SCROLL_STEPS:
                    break
                page.mouse.wheel(0, 1900)
                after_scroll = _read_stable_dom(page)
                after_ads = [_extract_card(card) for card in after_scroll.get("cards", [])]
                current_count = len({
                    _ad_key(normalized)
                    for item in after_ads
                    if (normalized := normalize_ad(item, keyword=keyword, country=country))
                } | set(cumulative))
                coverage.scroll_steps += 1
                if current_count <= last_count:
                    idle_steps += 1
                else:
                    idle_steps = 0
                last_count = current_count
                if idle_steps >= IDLE_SCROLL_STEPS_TO_STOP:
                    coverage.stopped_after_idle_steps = True
                    break
            _wait_for_stable_page(page)
            coverage.page_title = page.title()
            coverage.final_url = page.url
            context.close()
        finally:
            browser.close()

    ads = list(cumulative.values())
    return LiveResult(ads=ads, coverage=coverage)


def run_read_only_validation(*, keyword: str = KEYWORD, country: str = COUNTRY) -> LiveResult:
    result = collect_live_ads(keyword=keyword, country=country)
    fields = sorted({key for ad in result.ads for key, value in ad.items() if value is not None})
    report = {
        "ads_returned": len(result.ads),
        "coverage": asdict(result.coverage),
        "available_fields": fields,
        "sample_ads": result.ads[:3],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return result


def run_live_capture(*, keyword: str = KEYWORD, country: str = COUNTRY) -> CaptureStats:
    """Explicit live capture entry point; never called by read-only validation."""
    result = collect_live_ads(keyword=keyword, country=country)
    return store_capture(result.ads, keyword=keyword, country=country)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--read-only", action="store_true", help="run the live validation without database writes (default)")
    group.add_argument("--capture", action="store_true", help="write one live capture to the V2 tables")
    parser.add_argument("--keyword", default=KEYWORD)
    parser.add_argument("--country", default=COUNTRY)
    args = parser.parse_args()
    try:
        if args.capture:
            print(json.dumps(asdict(run_live_capture(keyword=args.keyword, country=args.country)), indent=2))
        else:
            run_read_only_validation(keyword=args.keyword, country=args.country)
    except Exception:
        logger.exception("Facebook Ad Library sensor failed.")
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - [SENSOR FACEBOOK ADS] - %(levelname)s - %(message)s")
    raise SystemExit(main())
