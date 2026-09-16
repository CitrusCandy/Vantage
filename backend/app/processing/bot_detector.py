from dataclasses import dataclass
import re
from typing import List, Optional, Tuple

from app.database.models import RawData


@dataclass
class BotDetectionResult:
    is_bot: bool
    reasons: List[str]


class BotDetector:
    """Heuristic-based detector for spam, automated bot promotions, and low-quality discourse."""

    SPAM_PATTERNS = [
        r"\b(?:free\s+crypto|airdrop|crypto\s+giveaway|presale\s+live)\b",
        r"\b(?:join\s+(?:our\s+)?telegram|t\.me/|t\.me\\)\b",
        r"\b(?:whatsapp\s+group|chat\.whatsapp\.com)\b",
        r"\b(?:buy\s+now|discount\s+code|promo\s+code|limited\s+time\s+offer)\b",
        r"\b(?:dm\s+for\s+rates|dm\s+to\s+buy|sugar\s+daddy|sugar\s+baby)\b",
        r"\b(?:casino\s+bonus|free\s+spins|betting\s+tips)\b",
        r"\b(?:100x\s+gem|moonshot|pump\s+and\s+dump)\b",
    ]

    BOT_USERNAMES = [
        r"^u/auto(?:moderator|mod)$",
        r"[-_]bot$",
        r"^bot[-_]",
        r"tracker[-_]bot",
        r"feed[-_]bot",
    ]

    def __init__(self, min_char_length: int = 20, min_word_count: int = 4):
        self.min_char_length = min_char_length
        self.min_word_count = min_word_count
        self.spam_regexes = [re.compile(p, re.IGNORECASE) for p in self.SPAM_PATTERNS]
        self.bot_user_regexes = [re.compile(p, re.IGNORECASE) for p in self.BOT_USERNAMES]

    def evaluate(self, item: RawData) -> BotDetectionResult:
        """Evaluate a RawData item against bot/spam heuristics."""
        reasons: List[str] = []
        text = item.text_content or ""
        cleaned = text.strip()

        # 1. Check minimum length
        words = cleaned.split()
        if len(cleaned) < self.min_char_length or len(words) < self.min_word_count:
            reasons.append(f"Insufficient length ({len(cleaned)} chars, {len(words)} words)")

        # 2. Check bot username patterns
        author = item.author_handle or ""
        for user_regex in self.bot_user_regexes:
            if user_regex.search(author):
                reasons.append(f"Bot author pattern: {author}")
                break

        # 3. Check spam/promo keyword triggers
        for spam_regex in self.spam_regexes:
            match = spam_regex.search(cleaned)
            if match:
                reasons.append(f"Spam keyword match: '{match.group(0)}'")
                break

        # 4. Check excessive link density
        urls = re.findall(r"https?://\S+|www\.\S+", cleaned)
        url_chars = sum(len(u) for u in urls)
        if len(cleaned) > 0 and (url_chars / len(cleaned)) > 0.45:
            reasons.append("Excessive URL link density")

        # 5. Check character/phrase repetition
        if len(words) >= 10:
            unique_words = len(set(w.lower() for w in words))
            diversity_ratio = unique_words / len(words)
            if diversity_ratio < 0.25:
                reasons.append(f"Extremely low vocabulary diversity ({diversity_ratio:.2f})")

        is_bot = len(reasons) > 0
        return BotDetectionResult(is_bot=is_bot, reasons=reasons)
