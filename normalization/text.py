"""Conservative Unicode-aware text normalization with a stable v1 profile."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True)
class NormalizationProfile:
    version: str = "deterministic_v1"
    unicode_form: str = "NFKC"
    casefold: bool = True


@dataclass(frozen=True)
class NormalizedText:
    raw_text: str
    normalized_text: str
    language_code: str | None
    version: str


DEFAULT_PROFILE = NormalizationProfile()

# Normalize typographic variants without deleting punctuation that may be part
# of an identifier, decimal, model, URL, or electrical specification.
_PUNCTUATION_MAP = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
}
_FRENCH_FUNCTION_WORDS = {
    "au", "aux", "avec", "ce", "ces", "chez", "dans", "de", "des",
    "du", "elle", "en", "et", "la", "le", "les", "mais", "ou", "pour",
    "par", "sur", "une", "un", "vous", "vos", "votre",
}
_ENGLISH_FUNCTION_WORDS = {
    "and", "are", "for", "from", "has", "have", "into", "is", "of",
    "the", "this", "to", "with", "your",
}


def detect_language(text: str) -> str | None:
    """Detect only high-confidence script/lexicon cases; otherwise return None."""
    letters = [char for char in text if unicodedata.category(char).startswith("L")]
    if len(letters) >= 3:
        arabic_letters = [
            char for char in letters if "ARABIC" in unicodedata.name(char, "")
        ]
        if len(arabic_letters) / len(letters) >= 0.65:
            return "ar"

    tokens = {token for token in _word_tokens(text)}
    french_count = len(tokens & _FRENCH_FUNCTION_WORDS)
    english_count = len(tokens & _ENGLISH_FUNCTION_WORDS)
    if french_count >= 2 and french_count > english_count:
        return "fr"
    if english_count >= 2 and english_count > french_count:
        return "en"
    return None


def _word_tokens(text: str) -> list[str]:
    token = []
    tokens: list[str] = []
    for char in text:
        if char.isalnum():
            token.append(char)
        elif token:
            tokens.append("".join(token))
            token.clear()
    if token:
        tokens.append("".join(token))
    return tokens


def normalize_text(raw_text: str | None, profile: NormalizationProfile = DEFAULT_PROFILE) -> NormalizedText:
    """Normalize visible text while retaining every meaningful token/punctuation.

    Unicode compatibility normalization, zero-width/control cleanup, quote/dash
    harmonization, Unicode whitespace collapse, and case folding are deterministic.
    Newlines and tabs become spaces; other control/format/surrogate code points
    are removed. Model/spec punctuation such as ``ABC-123``, ``1.5L`` and ``220V``
    is preserved.
    """
    if not profile.version.strip():
        raise ValueError("normalization profile needs an explicit version")
    raw = "" if raw_text is None else str(raw_text)
    canonical = unicodedata.normalize(profile.unicode_form, raw)
    visible: list[str] = []
    for char in canonical:
        category = unicodedata.category(char)
        if char in "\r\n\t\v\f" or char.isspace():
            visible.append(" ")
        elif category in {"Cc", "Cf", "Cs"}:
            continue
        else:
            visible.append(_PUNCTUATION_MAP.get(char, char))
    collapsed = " ".join("".join(visible).split())
    if profile.casefold:
        collapsed = collapsed.casefold()
    return NormalizedText(
        raw_text=raw,
        normalized_text=collapsed,
        language_code=detect_language(collapsed),
        version=profile.version,
    )
