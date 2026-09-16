import html
import re
from typing import Set
import unicodedata


def clean_text_for_display(text: str) -> str:
    """Basic cleanup preserving sentence structure for downstream synthesis."""
    if not text:
        return ""
    # Unescape HTML entities
    text = html.unescape(text)
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Normalize unicode characters
    text = unicodedata.normalize("NFKC", text)
    # Normalize excess whitespace and linebreaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_text_for_matching(text: str) -> str:
    """Normalize text aggressively for deduplication and shingling."""
    if not text:
        return ""
    text = clean_text_for_display(text)
    # Remove URLs
    text = re.sub(r"https?://\S+|www\.\S+", "", text)
    # Remove user handles and hashtags for content matching
    text = re.sub(r"[@#]\w+", "", text)
    # Lowercase
    text = text.lower()
    # Replace punctuation and special characters with whitespace
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse multiple whitespaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_shingles(text: str, k: int = 3, word_level: bool = False) -> Set[str]:
    """Generate k-shingles (character or word n-grams) from normalized text."""
    normalized = normalize_text_for_matching(text)
    if not normalized:
        return set()

    if word_level:
        words = normalized.split()
        if len(words) < k:
            return {" ".join(words)} if words else set()
        return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}
    else:
        # Character-level shingles
        if len(normalized) < k:
            return {normalized}
        return {normalized[i : i + k] for i in range(len(normalized) - k + 1)}
